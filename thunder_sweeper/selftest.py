"""Built-in self-test: ``./sweeper selftest`` (add ``--live`` for API checks).

Everything runs against a throw-away temp data home, so it never touches real
scan results, categories or tokens.  ``--live`` additionally talks to 迅雷 with
the saved credentials, creating a temporary ``/_sweeper_selftest_*`` folder and
moving/renaming/trashing it (never touching other files).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from contextlib import contextmanager

from . import categories, classify, dedupe, organize, screenshots, thunder_api, util


class Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures: list[tuple[str, str]] = []

    def check(self, name: str, cond: bool, detail: str = "") -> bool:
        if cond:
            self.passed += 1
            print(f"  ✓ {name}")
        else:
            self.failed += 1
            self.failures.append((name, detail))
            print(f"  ✗ {name}" + (f"  — {detail}" if detail else ""))
        return bool(cond)

    def eq(self, name: str, got, want) -> bool:
        return self.check(name, got == want, f"got={got!r} want={want!r}")

    def run(self, name: str, fn) -> None:
        try:
            fn()
        except Exception as exc:  # a raised error is a failed check
            self.failed += 1
            self.failures.append((name, repr(exc)))
            print(f"  ✗ {name}  — 抛出异常: {exc}")
            print("    " + traceback.format_exc().replace("\n", "\n    ")[:1200])

    def section(self, title: str) -> None:
        print(f"\n── {title} ──")

    def summary(self) -> int:
        total = self.passed + self.failed
        print(f"\n结果：{self.passed}/{total} 通过" + (f"，{self.failed} 失败" if self.failed else ""))
        if self.failures:
            print("\n失败项：")
            for name, detail in self.failures:
                print(f"  - {name}: {detail}")
        return 0 if self.failed == 0 else 1


_PATCH_ATTRS = (
    "ROOT", "DATA_DIR", "THUMB_DIR", "TOKENS_FILE", "VIDEOS_FILE", "FILES_FILE",
    "CLASSIFIED_FILE", "CLASSIFY_RULES_FILE", "MANUAL_CATS_FILE", "CATEGORIES_FILE",
    "QUEUE_FILE", "SHOTS_STATE_FILE", "SELECTIONS_FILE", "SCAN_STATE_FILE",
    "REVIEW_PROGRESS_FILE", "CHROME_PROFILE", "CONFIG_FILE", "BACKUP_DIR",
)


@contextmanager
def isolated_home(prefix: str = "sweeper-selftest-"):
    """Point util's paths at a temp dir, then restore them."""
    tmp = tempfile.mkdtemp(prefix=prefix)
    saved = {a: getattr(util, a) for a in _PATCH_ATTRS if hasattr(util, a)}
    saved_base = list(classify._BASE_CACHE)
    try:
        data = os.path.join(tmp, "data")
        os.makedirs(data, exist_ok=True)
        util.ROOT = util.Path(tmp)
        util.DATA_DIR = util.Path(data)
        util.THUMB_DIR = util.Path(data) / "thumbs"
        util.TOKENS_FILE = util.Path(data) / "tokens.json"
        util.VIDEOS_FILE = util.Path(data) / "videos.json"
        util.FILES_FILE = util.Path(data) / "files.json"
        util.CLASSIFIED_FILE = util.Path(data) / "classified.json"
        util.CLASSIFY_RULES_FILE = util.Path(data) / "classify_rules.json"
        util.MANUAL_CATS_FILE = util.Path(data) / "manual_categories.json"
        util.CATEGORIES_FILE = util.Path(data) / "categories.json"
        util.QUEUE_FILE = util.Path(data) / "queue.json"
        util.SHOTS_STATE_FILE = util.Path(data) / "shots_state.json"
        util.SELECTIONS_FILE = util.Path(data) / "selections.json"
        util.SCAN_STATE_FILE = util.Path(data) / "scan_state.json"
        util.REVIEW_PROGRESS_FILE = util.Path(data) / "review_progress.json"
        util.CHROME_PROFILE = util.Path(tmp) / ".chrome-profile"
        util.CONFIG_FILE = util.Path(tmp) / "config.json"
        util.BACKUP_DIR = util.Path(data) / "backups"
        classify._BASE_CACHE.clear()
        util.ensure_dirs()
        yield util.ROOT
    finally:
        for a, v in saved.items():
            setattr(util, a, v)
        classify._BASE_CACHE[:] = saved_base
        shutil.rmtree(tmp, ignore_errors=True)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http(method: str, url: str, body=None, timeout: float = 10):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        if "json" in ctype:
            return resp.status, json.loads(raw.decode("utf-8"))
        return resp.status, raw


# --------------------------------------------------------------------------- #
# sections
# --------------------------------------------------------------------------- #
def test_util(r: Results) -> None:
    r.section("util（路径 / 日志 / IO / 配置）")
    with isolated_home():
        r.eq("人类可读大小", util.human_size(1536), "1.5 KB")
        r.eq("人类可读时长", util.human_duration(3725), "1:02:05")
        r.eq("时长空值", util.human_duration(None), "-")
        r.check("cpu_count>0", util.cpu_count() >= 1)
        r.eq("cap_workers 上限", util.cap_workers(10_000), util.cpu_count())
        r.eq("cap_workers 下限", util.cap_workers(0), 1)

        util.atomic_write_json(util.CATEGORIES_FILE, {"a": 1})
        r.eq("原子写入可读回", util.read_json(util.CATEGORIES_FILE), {"a": 1})
        r.eq("缺失文件取默认", util.read_json(util.DATA_DIR / "nope.json", {"d": 1}), {"d": 1})
        (util.DATA_DIR / "bad.json").write_text("{not json", encoding="utf-8")
        r.eq("损坏 JSON 取默认", util.read_json(util.DATA_DIR / "bad.json", "fallback"), "fallback")

        for i in range(5):
            util.atomic_write_json(util.CATEGORIES_FILE, {"i": i})
        backups = list(util.BACKUP_DIR.glob("categories.json.*.bak"))
        r.check("改动用户文件会自动备份", len(backups) >= 4, f"backups={len(backups)}")
        r.check("备份数量受 keep 限制", len(backups) <= 20)

        util.atomic_write_json(util.CONFIG_FILE, {"organize_base": "/我的整理", "workers": 2})
        cfg = util.load_config()
        r.eq("用户配置覆盖默认", cfg["organize_base"], "/我的整理")
        r.eq("未指定项沿用默认", cfg["debug_port"], util.DEFAULT_CONFIG["debug_port"])

        os.environ["THUNDER_SWEEPER_HOME"] = "/tmp/ts-home-test"
        try:
            r.eq("环境变量指定数据目录", str(util._default_home()), os.path.realpath("/tmp/ts-home-test"))
        finally:
            os.environ.pop("THUNDER_SWEEPER_HOME", None)

        frozen_orig = getattr(sys, "frozen", None)
        sys.frozen = True  # type: ignore[attr-defined]
        try:
            home = util._default_home()
            r.check("打包运行时数据目录在用户目录", str(util.SOURCE_ROOT) not in str(home),
                    f"home={home}")
        finally:
            if frozen_orig is None:
                try:
                    del sys.frozen  # type: ignore[attr-defined]
                except AttributeError:
                    pass
            else:
                sys.frozen = frozen_orig  # type: ignore[attr-defined]

        r.check("Chrome 探测不抛异常", util.find_chrome({}) is None or isinstance(util.find_chrome({}), str))
        r.check("版本号非空", bool(util.app_version()))


def test_categories(r: Results) -> None:
    r.section("categories（分类树）")
    with isolated_home():
        tree = categories.load_tree()
        r.eq("默认 6 个顶级分类", len(tree), 6)
        r.eq("默认 ids", categories.ids(), {"jp", "west", "cn", "adult_other", "non_adult", "unknown"})

        child = categories.add_node("cn", "自拍")
        r.eq("新增子分类 id 生成", child["id"][0], "c")
        flat = {x["id"]: x for x in categories.flat()}
        r.eq("子分类 parent", flat[child["id"]]["parent"], "cn")
        r.eq("子分类 path", flat[child["id"]]["path"], ["国产", "自拍"])
        r.eq("子分类 depth", flat[child["id"]]["depth"], 1)

        grand = categories.add_node(child["id"], "合集")
        flat = {x["id"]: x for x in categories.flat()}
        r.eq("三级 path", flat[grand["id"]]["path"], ["国产", "自拍", "合集"])

        categories.rename_node(child["id"], "自拍精选")
        r.eq("改名生效", categories.find(child["id"])["name"], "自拍精选")

        removed = categories.delete_node(child["id"])
        r.eq("删除连同子孙", set(removed), {child["id"], grand["id"]})
        r.check("删除后不存在", categories.find(child["id"]) is None)

        try:
            categories.add_node("no_such_parent", "x")
            r.check("无效父分类报错", False)
        except ValueError:
            r.check("无效父分类报错", True)

        try:
            categories.add_node(None, "   ")
            r.check("空名称报错", False)
        except ValueError:
            r.check("空名称报错", True)

        r.eq("LABELS 中文名", categories.labels()["jp"], "日本")
        r.check("RESERVED 保留", categories.RESERVED == {"unknown", "non_adult"})


def test_classify(r: Results) -> None:
    r.section("classify（分类规则）")
    with isolated_home():
        r.check("字母边界：jul 不匹配 julesjordan",
                classify._kw_hit("julesjordan", ["jul"]) is False)
        r.check("字母边界：aavv 匹配 aavv121",
                classify._kw_hit("aavv121", ["aavv"]) is True)
        r.check("含数字关键词按子串匹配",
                classify._kw_hit("abc123def", ["123"]) is True)
        r.check("中文关键词子串匹配",
                classify._kw_hit("国产自拍视频", ["自拍"]) is True)

        cat, _ = classify.classify("ABP-123.mp4", "/下载")
        r.eq("番号兜底→日本", cat, "jp")
        cat, _ = classify.classify("My Happy Holiday 2019.mp4", "/下载")
        r.eq("纯英文多词→欧美", cat, "west")
        cat, _ = classify.classify("某国产自拍视频.mp4", "/下载")
        r.eq("中文→国产优先于日本", cat, "cn")
        cat, _ = classify.classify("readme.txt", "/下载")
        r.eq("文档→非成人", cat, "non_adult")
        cat, _ = classify.classify("clip.mp4", "/下载")
        r.eq("无法判断→未知", cat, "unknown")

        cat, reason = classify.classify("whatever.mp4", "/整理/日本")
        r.eq("/整理 内只用文件名（不因路径判日本）", cat, "unknown")
        r.check("原因串非空", bool(reason))

        cat, reason = classify.classify("ABP-123.mp4", "/整理/欧美")
        r.eq("/整理 内番号兜底→日本", cat, "jp")

        rules = {"extra": {"west": ["myhappy"]}, "overrides": [{"keyword": "special", "category": "cn"}]}
        cat, reason = classify.classify("special-video.mp4", "/下载", rules)
        r.eq("用户 override 最高优先", (cat, reason), ("cn", "override:special"))

        cat, _ = classify.classify("plain-file-xyz.mp4", "/下载", {"extra": {"jp": ["plain"]}, "overrides": []})
        r.eq("自定义关键词覆盖内置", cat, "jp")

        files = [
            {"id": "1", "name": "ABP-123.mp4", "path": "/下载", "size": 10},
            {"id": "2", "name": "other.mp4", "path": "/下载", "size": 20},
        ]
        classify.save_manual({"2": "west"})
        out = classify.categorize_files(files)
        by = {c["id"]: c for c in out}
        r.eq("手动分类覆盖自动", by["2"]["category"], "west")
        r.check("手动标记", by["2"]["manual"] is True)
        r.eq("自动分类字段保留", by["2"]["auto_category"], "unknown")
        r.check("非手动标记 false", by["1"]["manual"] is False)
        stats = classify.summarize(out)
        r.eq("汇总计数", stats["jp"]["count"], 1)


def test_organize(r: Results) -> None:
    r.section("organize（整理方案）")
    with isolated_home():
        files = [
            {"id": "rootfile", "name": "ABP-123.mp4", "path": "/", "size": 500 * 1024**2},
            {"id": "infolder", "name": "XYZ-999.mp4", "path": "/下载", "size": 300 * 1024**2},
            {"id": "inplace", "name": "ABC-001.mp4", "path": "/整理/日本", "size": 100 * 1024**2},
            {"id": "wrong", "name": "DEF-002.mp4", "path": "/整理/欧美", "size": 100 * 1024**2},
            {"id": "doc", "name": "notes.txt", "path": "/下载", "size": 1024},
        ]
        classified = [
            {"id": "rootfile", "category": "jp"},
            {"id": "infolder", "category": "jp"},
            {"id": "inplace", "category": "jp"},
            {"id": "wrong", "category": "jp"},
            {"id": "doc", "category": "unknown"},
        ]
        plan = organize.build_plan(files, classified, base="/整理")
        moved = {m["id"]: m for m in plan["moves"]}
        r.check("根目录视频被移动", "rootfile" in moved)
        r.eq("移动目标=整理/日本", moved["rootfile"]["to"], "/整理/日本")
        r.check("子目录视频被移动", "infolder" in moved)
        r.check("已在目标位置不动", "inplace" not in moved)
        r.check("已在内但错归：默认不移动", "wrong" not in moved)
        r.eq("错归进入 mismatch 清单", plan["summary"]["mismatch_count"], 1)
        r.check("unknown 不移动", "doc" not in moved)

        plan_fix = organize.build_plan(files, classified, base="/整理", fix_inside=True)
        fix_ids = {m["id"] for m in plan_fix["moves"]}
        r.check("fix_inside 移动错归文件", "wrong" in fix_ids)
        r.eq("fix_inside 目标", {m["id"]: m["to"] for m in plan_fix["moves"]}["wrong"], "/整理/日本")

        r.eq("源目录仅剩视频→删除", [d["path"] for d in plan["delete_folders"]], ["/下载"])
        r.eq("删除时附带的垃圾计数", plan["summary"]["delete_extra_count"], 1)

        files2 = [dict(files[1]), {"id": "big_other", "name": "movie.mkv", "path": "/下载",
                                   "size": 2000 * 1024**2}]
        plan2 = organize.build_plan(files2, classified, base="/整理")
        r.check("源目录含大文件→保留", any(k["path"] == "/下载" for k in plan2["keep_folders"]))

        r.check("根目录永不删除", all(d["path"] != "/" for d in plan["delete_folders"]))

        files3 = [
            {"id": "a1", "name": "ABP-100.mp4", "path": "/x", "size": 200 * 1024**2},
            {"id": "a2", "name": "ABP-100.mp4", "path": "/y", "size": 200 * 1024**2},
        ]
        plan3 = organize.build_plan(files3, [{"id": "a1", "category": "jp"}, {"id": "a2", "category": "jp"}])
        names = sorted(m["target_name"] for m in plan3["moves"])
        r.eq("重名自动加序号", names, ["ABP-100 (2).mp4", "ABP-100.mp4"])

        syn = organize.build_plan(files2, classified, base="/整理", move_cats={"jp"})
        r.check("move_cats 限定生效", all(m["category"] == "jp" for m in syn["moves"]))

        r.check("is_significant 大文件", organize.is_significant({"name": "a.mp4", "size": 500 * 1024**2}, 20, 100))
        r.check("is_significant 小垃圾不算", not organize.is_significant({"name": "a.torrent", "size": 1 * 1024**2}, 20, 100))
        r.check("系统错误识别", organize._is_system_error("file_operate_system_folder: xxx"))


def test_dedupe(r: Results) -> None:
    r.section("dedupe（重复检测）")
    with isolated_home():
        videos = [
            {"id": "1", "name": "ABP-123.mp4", "size": 1000, "path": "/a"},
            {"id": "2", "name": "ABP-123 (1).mp4", "size": 1000, "path": "/b"},
            {"id": "3", "name": "ABP-123.mp4", "size": 999, "path": "/c"},
            {"id": "4", "name": "Other.mp4", "size": 5000, "path": "/d"},
        ]
        groups = dedupe.find_duplicates(videos)
        r.eq("发现 1 组重复", len(groups), 1)
        payload = dedupe.group_payload(groups)
        r.eq("组内数量", payload[0]["count"], 2)
        r.eq("可省容量", payload[0]["wasted"], 1000)
        r.check("唯一文件不成组", all(g["count"] > 1 for g in payload))


def test_thunder_api(r: Results) -> None:
    r.section("thunder_api（action 推导）")
    cases = [
        ("POST", "https://api-pan.xunlei.com/drive/v1/files:batchMove", "post:/drive/v1/files:batchMove"),
        ("POST", "https://api-pan.xunlei.com/drive/v1/files", "post:/drive/v1/files"),
        ("PATCH", "https://api-pan.xunlei.com/drive/v1/files/ABC/trash", "patch:/drive/v1/files/ABC/trash"),
        ("GET", "https://api-pan.xunlei.com/drive/v1/tasks?id=1", "get:/drive/v1/tasks"),
        ("GET", "https://api-pan.xunlei.com/drive/v1/files", "get:/drive/v1/files"),
    ]
    for method, url, want in cases:
        r.eq(f"action {method} {url.split('xunlei.com')[-1][:34]}", thunder_api.ThunderAPI._action(method, url), want)


def test_captcha_provider(r: Results) -> None:
    r.section("chrome_tokens（按 action 缓存 captcha）")
    from . import chrome_tokens as ct

    with isolated_home():
        calls: list[dict] = []

        class _Resp:
            def __init__(self, payload):
                self._p = payload

            def json(self):
                return self._p

        def fake_post(url, headers=None, data=None, timeout=None):
            body = json.loads(data)
            calls.append(body)
            return _Resp({"captcha_token": "tok-" + body["action"], "expires_in": 300})

        cfg = dict(util.DEFAULT_CONFIG)
        store = {
            "credentials.access_token": "a", "credentials.refresh_token": "r",
            "credentials.expires_at": int(time.time()) + 10000,
            "captcha.client_id": "cid", "captcha.device_id": "did",
            "captcha.captcha_sign": "s", "captcha.client_version": "v",
            "captcha.package_name": "p", "captcha.timestamp": "t", "captcha.user_id": "u",
        }
        saved = ct.requests.post
        ct.requests.post = fake_post
        try:
            p = ct.TokenProvider(store, cfg)
            t1 = p.captcha_token("post:/drive/v1/files:batchMove")
            t2 = p.captcha_token("post:/drive/v1/files:batchMove")
            r.eq("同一 action 命中缓存", (t1, len(calls)), (t2, 1))
            t3 = p.captcha_token("post:/drive/v1/files")
            r.eq("不同 action 分别申请", (t3, len(calls)), ("tok-post:/drive/v1/files", 2))
            r.eq("init 请求携带对应 action", calls[-1]["action"], "post:/drive/v1/files")
            p.invalidate_captcha("post:/drive/v1/files")
            p.captcha_token("post:/drive/v1/files")
            r.eq("失效后重新申请", len(calls), 3)
            h = p.headers("get:/drive/v1/files")
            r.eq("请求头含 captcha", h["x-captcha-token"], "tok-get:/drive/v1/files")
            r.check("请求头含 Bearer", h["Authorization"].startswith("Bearer "))
            r.check("自检写入被隔离在临时目录",
                    str(util.TOKENS_FILE).startswith(str(util.ROOT))
                    and (util.read_json(util.TOKENS_FILE) or {}).get("captcha.client_id") == "cid")
        finally:
            ct.requests.post = saved


def test_scan_walk(r: Results) -> None:
    r.section("scan（递归遍历 / 断点续扫，模拟 API）")

    class FakeAPI:
        cfg = {"api_delay": 0}

        def __init__(self, tree):
            self.tree = tree

        def list_folder(self, parent_id):
            return self.tree.get(parent_id, [])

    def vid(i, name):
        return {"id": i, "name": name, "kind": "drive#file", "mime_type": "video/mp4",
                "size": 100, "video": {"duration": 10}}

    tree = {
        "": [{"id": "f1", "name": "F1", "kind": "drive#folder"},
             vid("v1", "A.mp4"),
             {"id": "d1", "name": "notes.txt", "kind": "drive#file", "size": 5}],
        "f1": [{"id": "f2", "name": "F2", "kind": "drive#folder"},
               vid("v2", "B.mp4")],
        "f2": [vid("v3", "C.mp4")],
    }
    state = thunder_api.ThunderAPI.walk(FakeAPI(tree), state={})
    r.eq("扫描到的视频数", len(state["videos"]), 3)
    r.eq("扫描到的全部条目数(含文件夹)", len(state["files"]), 6)
    r.eq("访问的目录数", state["dirs"], 3)
    r.check("视频带上路径", {v["name"]: v["path"] for v in state["videos"]}["C.mp4"] == "/F1/F2")

    half = {"videos": [{"id": "v9", "name": "Z.mp4", "path": "/", "size": 1}],
            "files": [], "visited": ["f2"], "frontier": [["f1", "/F1"]], "dirs": 1}
    resumed = thunder_api.ThunderAPI.walk(FakeAPI(tree), state=half)
    r.check("续扫保留已有结果", any(v["id"] == "v9" for v in resumed["videos"]))
    r.check("续扫继续收集", any(v["id"] == "v2" for v in resumed["videos"]))


def test_ffmpeg_pipeline(r: Results) -> None:
    r.section("截图管线（用内置 ffmpeg 真实生成并截帧）")
    import subprocess
    import tempfile

    with isolated_home():
        exe = screenshots._ffmpeg_exe()
        if not exe:
            r.check("ffmpeg 可用", False, "未找到 ffmpeg")
            return
        tmp = tempfile.mkdtemp(prefix="sweeper-media-")
        try:
            src = os.path.join(tmp, "test.mp4")
            gen = subprocess.run(
                [exe, "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                 "-t", "3", "-pix_fmt", "yuv420p", src],
                capture_output=True, text=True)
            ok = gen.returncode == 0 and os.path.exists(src) and os.path.getsize(src) > 0
            r.check("生成测试视频", ok, gen.stderr[-200:] if not ok else "")
            if not ok:
                return
            cfg = util.load_config()
            out = util.THUMB_DIR / "vt" / "1.jpg"
            status = screenshots.grab(src, 1.0, out, cfg)
            r.eq("截取指定时间点", status, "ok")
            r.check("输出非空 jpg", out.exists() and out.stat().st_size > 0)
            meta = screenshots.probe(src, cfg)
            r.check("探测到时长", bool(meta.get("duration")), f"meta={meta}")
            r.check("探测到分辨率", meta.get("width") == 320 and meta.get("height") == 240, f"meta={meta}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def test_screenshots(r: Results) -> None:
    r.section("screenshots（截图辅助）")
    with isolated_home():
        exe = screenshots._ffmpeg_exe()
        r.check("ffmpeg 可用（PATH 或 imageio-ffmpeg）", bool(exe), f"exe={exe}")
        folder = util.THUMB_DIR / "vid1"
        folder.mkdir(parents=True, exist_ok=True)
        for i in (1, 2, 3):
            (folder / f"{i}.jpg").write_bytes(b"x" * 10)
        thumbs = screenshots.disk_thumbs("vid1", 8)
        r.eq("已存在的缩略图数量", len(thumbs), 3)
        r.check("is_complete 未满", screenshots.is_complete({"id": "vid1"}, 8) is False)
        r.check("is_complete 已满", screenshots.is_complete({"id": "vid1"}, 3) is True)
        r.eq("无目录返回空", screenshots.disk_thumbs("nope", 8), [])


def test_review_server(r: Results) -> None:
    r.section("review_server（网页接口，端到端）")
    with isolated_home():
        from . import review_server

        videos = [
            {"id": "V1", "name": "ABP-123.mp4", "path": "/下载", "size": 100,
             "category": "jp", "auto_category": "jp", "manual": False, "thumbs": []},
            {"id": "V2", "name": "random.mp4", "path": "/下载", "size": 200,
             "category": "unknown", "auto_category": "unknown", "manual": False, "thumbs": []},
        ]
        util.atomic_write_json(util.VIDEOS_FILE, videos)
        util.atomic_write_json(util.FILES_FILE, videos)
        classify.save_manual({})

        port = _free_port()
        thread = threading.Thread(
            target=review_server.serve, kwargs={"videos": videos, "port": port, "open_browser": False},
            daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                status, _ = _http("GET", base + "/")
                break
            except Exception:
                time.sleep(0.2)
        else:
            r.check("网页服务启动", False, "超时")
            return
        r.eq("首页 200", status, 200)
        _, page = _http("GET", base + "/")
        r.check("首页包含文件清单数据", b"VIDEOS" in page and b"ABP-123" in page)

        status, cat = _http("GET", base + "/categories")
        r.eq("/categories 200", status, 200)
        r.check("分类树返回", len(cat["categories"]) >= 6)

        status, j = _http("POST", base + "/manual", {"id": "V2", "category": "cn"})
        r.check("/manual 成功", status == 200 and j.get("category") == "cn")
        disk = {c["id"]: c for c in util.read_json(util.CLASSIFIED_FILE)}
        r.eq("手动分类写回 classified.json", disk["V2"]["category"], "cn")

        status, _ = _http("POST", base + "/mark", {"index": 1, "id": "V1", "name": "ABP-123.mp4"})
        r.eq("/mark 200", status, 200)
        r.eq("进度已落盘", (util.read_json(util.REVIEW_PROGRESS_FILE) or {}).get("id"), "V1")

        status, j = _http("POST", base + "/categories/add", {"parent_id": "cn", "name": "测试子类"})
        r.check("新增分类成功", status == 200 and any(x["name"] == "测试子类" for x in categories.flat()))

        status, j = _http("POST", base + "/submit", {"delete": ["V1"], "replace": True})
        r.eq("/submit 200", status, 200)
        r.eq("选择已落盘", (util.read_json(util.SELECTIONS_FILE) or {}).get("delete"), ["V1"])

        status, plan = _http("GET", base + "/organize")
        r.check("/organize 返回方案", status == 200 and plan["ok"] and "summary" in plan["plan"])

        try:
            _http("GET", base + "/thumb/V1/9.jpg")
            r.check("缺失缩略图 404", False)
        except urllib.error.HTTPError as exc:
            r.eq("缺失缩略图 404", exc.code, 404)

        _http("POST", base + "/submit", {"delete": [], "replace": True})


def test_review_http_edge(r: Results) -> None:
    r.section("review_server（异常输入 / 注入防护）")
    with isolated_home():
        from . import review_server

        evil = {"id": "E1", "name": "</script><img src=x onerror=alert(1)>.mp4",
                "path": "/下载", "size": 1, "category": "jp", "auto_category": "jp",
                "manual": False, "thumbs": []}
        videos = [evil]
        util.atomic_write_json(util.VIDEOS_FILE, videos)
        util.atomic_write_json(util.FILES_FILE, videos)
        port = _free_port()
        threading.Thread(target=review_server.serve,
                         kwargs={"videos": videos, "port": port, "open_browser": False},
                         daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                _http("GET", base + "/")
                break
            except Exception:
                time.sleep(0.2)
        _, page = _http("GET", base + "/")
        body = page.decode("utf-8", "ignore")
        r.check("恶意文件名不会闭合 script", "</script><img" not in body)
        r.check("已转义为 \\u003c", "\\u003c" in body)

        try:
            _http("POST", base + "/manual", {"id": "NOPE", "category": "jp"})
            r.check("未知 id 返回 404", False)
        except urllib.error.HTTPError as exc:
            r.eq("未知 id 返回 404", exc.code, 404)

        status, _ = _http("POST", base + "/manual", {"id": "E1", "category": "jp",
                                                     "extra": "ignored"})
        r.eq("多余字段被忽略", status, 200)

        try:
            status, j = _http("POST", base + "/organize/apply", {"apply": False})
        except urllib.error.HTTPError as exc:
            status, j = exc.code, {}
        r.check("未勾选任何操作时给出提示", status in (400, 501) or j.get("ok") is False)

        status, j = _http("GET", base + "/apply/status")
        r.check("/apply/status 可用", status == 200 and "running" in j)

        try:
            _http("GET", base + "/definitely/missing")
            r.check("未知路径 404", False)
        except urllib.error.HTTPError as exc:
            r.eq("未知路径 404", exc.code, 404)

        status, _ = _http("POST", base + "/categories/delete", {"id": "unknown"})
        r.check("删除保留分类安全处理", status in (200, 400))


def test_request_retry(r: Results) -> None:
    r.section("thunder_api（401 / captcha 失效自动重试）")
    from . import thunder_api as ta

    class Resp:
        def __init__(self, status, text="{}"):
            self.status_code = status
            self.text = text

        def json(self):
            return json.loads(self.text)

    class FakeSession:
        def __init__(self, script):
            self.script = list(script)
            self.calls = 0

        def request(self, *a, **k):
            self.calls += 1
            return self.script.pop(0)

    class FakeProvider:
        def __init__(self):
            self.refreshed = 0
            self.invalidated = []

        def headers(self, action=None):
            return {"x-captcha-token": "t"}

        def force_refresh(self):
            self.refreshed += 1

        def invalidate_captcha(self, action=None):
            self.invalidated.append(action)

    cfg = dict(util.DEFAULT_CONFIG)
    cfg["api_retries"] = 3
    cfg["api_delay"] = 0

    api = ta.ThunderAPI(FakeProvider(), cfg)
    api.session = FakeSession([Resp(401, '{"error":"unauthenticated"}'), Resp(200, '{"files":[]}')])
    try:
        out = api.list_folder("")
        r.eq("401 后刷新并重试成功", (api.provider.refreshed, out), (1, []))
    except Exception as exc:
        r.check("401 后刷新并重试成功", False, str(exc))

    api = ta.ThunderAPI(FakeProvider(), cfg)
    api.session = FakeSession([Resp(400, '{"error":"captcha_invalid"}'), Resp(200, '{"files":[]}')])
    try:
        api.list_folder("")
        r.check("captcha_invalid 后重新申请重试",
                api.provider.invalidated == ["get:/drive/v1/files"], str(api.provider.invalidated))
    except Exception as exc:
        r.check("captcha_invalid 后重新申请重试", False, str(exc))

    api = ta.ThunderAPI(FakeProvider(), cfg)
    api.session = FakeSession([Resp(403, '{"error":"forbidden"}')])
    try:
        api.list_folder("")
        r.check("其它错误直接抛出", False)
    except RuntimeError as exc:
        r.check("其它错误直接抛出", "403" in str(exc))


def test_cli(r: Results) -> None:
    r.section("CLI（打包/源码两种运行方式）")
    import subprocess

    if getattr(sys, "frozen", False):
        cmd_prefix = [sys.executable]
    else:
        cmd_prefix = [sys.executable, "-m", "thunder_sweeper"]
    tmp = tempfile.mkdtemp(prefix="sweeper-cli-")
    env = dict(os.environ, THUNDER_SWEEPER_HOME=tmp)
    try:
        out = subprocess.run(cmd_prefix + ["--version"], capture_output=True, text=True, env=env)
        r.eq("--version 退出码", out.returncode, 0)
        r.check("--version 输出程序名", "ThunderSweeper" in (out.stdout + out.stderr))

        out = subprocess.run(cmd_prefix + ["-h"], capture_output=True, text=True, env=env)
        r.eq("-h 退出码", out.returncode, 0)
        for cmd in ("login", "scan", "classify", "organize", "review", "selftest"):
            r.check(f"帮助含子命令 {cmd}", cmd in out.stdout)

        out = subprocess.run(cmd_prefix + ["no_such_command"], capture_output=True, text=True, env=env)
        r.check("未知子命令非零退出", out.returncode != 0)

        out = subprocess.run(cmd_prefix + ["status"], capture_output=True, text=True, env=env)
        r.eq("status 空数据可运行", out.returncode, 0)

        out = subprocess.run(cmd_prefix + ["organize"], capture_output=True, text=True, env=env)
        r.check("无扫描数据时也能预览（不崩溃）",
                out.returncode == 0 and "移动" in (out.stdout + out.stderr),
                f"rc={out.returncode} out={(out.stdout + out.stderr)[:150]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_live(r: Results) -> None:
    r.section("live（真实迅雷 API 沙箱：只动自建测试文件夹）")
    from . import chrome_tokens

    cfg = util.load_config()
    try:
        provider = chrome_tokens.load_provider(cfg)
    except Exception as exc:
        r.check("已登录（跳过 live 测试）", False, str(exc))
        return
    api = thunder_api.ThunderAPI(provider, cfg)

    tag = time.strftime("_sweeper_selftest_%Y%m%d_%H%M%S")
    created: list[str] = []
    try:
        root = api.list_folder("")
        r.check("可读取根目录", isinstance(root, list))

        name_a = tag + "_A"
        name_b = tag + "_B"
        id_a = api.create_folder(name_a, "")
        id_b = api.create_folder(name_b, "")
        created += [id_a, id_b]
        r.check("创建文件夹 A/B", bool(id_a) and bool(id_b))

        names = {e.get("name") for e in api.list_folder("")}
        r.check("新建文件夹可见", {name_a, name_b} <= names, f"names={sorted(names)[:5]}")

        api.rename(id_b, name_b + "_renamed")
        names = {e.get("name") for e in api.list_folder("")}
        r.check("rename 生效", (name_b + "_renamed") in names)

        res = api.batch_move([id_a], id_b)
        r.check("batchMove 返回 task", bool(res.get("task_id")))
        if res.get("task_id"):
            for _ in range(30):
                task = api.get_task(res["task_id"])
                phase = (task.get("phase") or "").upper()
                if not task or "COMPLETE" in phase or "ERROR" in phase or "FAIL" in phase:
                    break
                time.sleep(0.5)
        kids = {e.get("name") for e in api.list_folder(id_b)}
        r.check("移动后 A 在 B 之内", name_a in kids, f"kids={sorted(kids)}")

        api.trash(id_a)
        api.trash(id_b)
        created.clear()
        time.sleep(1)
        names = {e.get("name") for e in api.list_folder("")}
        r.check("测试文件夹已清理", name_a not in names and (name_b + "_renamed") not in names)
    finally:
        for fid in created:
            try:
                api.trash(fid)
            except Exception:
                pass


def run(live: bool = False) -> int:
    print(f"ThunderSweeper 自检 v{util.app_version()}  (python {sys.version.split()[0]}, "
          f"{sys.platform})")
    r = Results()
    for name, fn in (
        ("util", test_util), ("categories", test_categories), ("classify", test_classify),
        ("organize", test_organize), ("dedupe", test_dedupe), ("thunder_api", test_thunder_api),
        ("captcha", test_captcha_provider), ("scan_walk", test_scan_walk),
        ("ffmpeg", test_ffmpeg_pipeline), ("screenshots", test_screenshots),
        ("review_server", test_review_server), ("review_http_edge", test_review_http_edge),
        ("request_retry", test_request_retry), ("cli", test_cli),
    ):
        r.run(name, lambda fn=fn: fn(r))
    if live:
        r.run("live", lambda: test_live(r))
    return r.summary()
