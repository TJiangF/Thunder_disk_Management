"""Disposable end-to-end verification harness (NOT shipped with the app).

Creates a self-contained sandbox on the real 迅雷 drive, seeds files, runs the
real scan → classify → organize pipeline against it, asserts the outcome, then
deletes everything it created.  Only ever touches files whose names start with
the sandbox prefix.

Usage:  .venv/bin/python tools/e2e_cloud.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from thunder_sweeper import (categories, chrome_tokens, classify, organize,  # noqa: E402
                             thunder_api, util)

STAMP = time.strftime("%y%m%d%H%M%S")
SANDBOX = f"_sweeper_e2e_{STAMP}"
PREFIX = "_sw_"

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}  {detail}")


def setup_home():
    tmp = tempfile.mkdtemp(prefix="sweeper-e2e-")
    util.ROOT = util.Path(tmp)
    util.DATA_DIR = util.Path(tmp) / "data"
    util.THUMB_DIR = util.DATA_DIR / "thumbs"
    util.VIDEOS_FILE = util.DATA_DIR / "videos.json"
    util.FILES_FILE = util.DATA_DIR / "files.json"
    util.CLASSIFIED_FILE = util.DATA_DIR / "classified.json"
    util.SCAN_STATE_FILE = util.DATA_DIR / "scan_state.json"
    util.CATEGORIES_FILE = util.DATA_DIR / "categories.json"
    util.CLASSIFY_RULES_FILE = util.DATA_DIR / "classify_rules.json"
    util.MANUAL_CATS_FILE = util.DATA_DIR / "manual_categories.json"
    util.CONFIG_FILE = util.Path(tmp) / "config.json"
    classify._BASE_CACHE.clear()
    util.atomic_write_json(util.CONFIG_FILE, {"organize_base": "/" + SANDBOX,
                                              "api_delay": 0.05})
    util.ensure_dirs()
    return tmp


def make_folder(api, name, parent=""):
    return api.create_folder(name, parent)


def upload_text(api, name, parent, text=b"hello"):
    """迅雷 has no simple public upload; use a tiny data-uri style file via the
    create API is unsupported, so we create *folders* and rely on the folder
    structure for classification + move semantics."""
    raise NotImplementedError


def main() -> int:
    cfg = util.load_config()
    provider = chrome_tokens.load_provider(cfg)
    api = thunder_api.ThunderAPI(provider, cfg)

    print(f"沙箱: /{SANDBOX}（前缀 {PREFIX}，结束后删除）")
    home = setup_home()
    created: list[str] = []
    try:
        root_id = make_folder(api, SANDBOX)
        created.append(root_id)
        print(f"  · 创建沙箱根目录 id={root_id}")
        check("创建沙箱根目录", bool(root_id))

        # fake a small drive: sandbox/{in_jp, big_other, nested/deep}
        sub = {}
        for name in ("日本A", "日本B", "欧美C", "带空格 与中文", "nested"):
            fid = make_folder(api, f"{PREFIX}{name}", root_id)
            created.append(fid)
            sub[name] = fid
        deep = make_folder(api, f"{PREFIX}深一层", sub["nested"])
        created.append(deep)
        check("创建沙箱子目录", len(sub) == 5)

        # 1) walk only the sandbox: make sure scan handles unicode + spaces
        class ScopedAPI:
            def __init__(self, api, root):
                self.api = api
                self.root = root
                self.cfg = api.cfg

            def list_folder(self, parent_id):
                return self.api.list_folder(self.root if parent_id == "" else parent_id)

        scoped = ScopedAPI(api, root_id)
        state = thunder_api.ThunderAPI.walk(scoped, state={})
        names = {f["name"] for f in state["files"]}
        check("扫描覆盖全部沙箱目录", {"_sw_nested", "_sw_深一层"} <= names, f"{sorted(names)}")
        check("中文/空格名不乱码", any("带空格 与中文" in n for n in names))

        # 2) classification of the sandbox folder names in a realistic tree
        fake_files = [
            {"id": "f1", "name": "ABP-123.mp4", "path": f"/{SANDBOX}/日本A", "size": 300 * 2**20},
            {"id": "f2", "name": "Sweet Couple 2019.mp4", "path": f"/{SANDBOX}/欧美C", "size": 200 * 2**20},
            {"id": "f3", "name": "junk.torrent", "path": f"/{SANDBOX}/日本B", "size": 1024},
        ]
        classified = classify.categorize_files(fake_files)
        cats = {c["id"]: c["category"] for c in classified}
        check("番号→日本", cats["f1"] == "jp", cats)
        check("英文名→欧美", cats["f2"] == "west", cats)

        # 3) build a plan targeting /<SANDBOX>/日本 inside the sandbox. Since the
        #    sandbox root is the organize base, everything is already "inside",
        #    so it lands in mismatch/_fix_inside — the correct behaviour.
        base = f"/{SANDBOX}"
        plan = organize.build_plan(fake_files, classified, base=base, move_cats={"jp", "west"})
        check("沙箱内文件若归类不符→进 mismatch 而非直接移动",
              plan["summary"]["move_count"] == 0 and plan["summary"]["mismatch_count"] == 2,
              plan["summary"])

        plan_fix = organize.build_plan(
            fake_files, classified, base=base, move_cats={"jp", "west"}, fix_inside=True)
        check("勾选纠错后产生移动到 /<沙箱>/日本 与 /<沙箱>/欧美",
              {m["to"] for m in plan_fix["moves"]} == {f"{base}/日本", f"{base}/欧美"},
              plan_fix["summary"])

        # 3b) an *outside* file must move into the sandbox base
        outside = [{"id": "f9", "name": "ABP-999.mp4", "path": "/", "size": 100 * 2**20}]
        plan_out = organize.build_plan(
            outside, [{"id": "f9", "category": "jp"}], base=base, move_cats={"jp"})
        check("沙箱外文件会移动到沙箱内",
              [m["to"] for m in plan_out["moves"]] == [f"{base}/日本"], plan_out["summary"])

        # 4) real move: put an outside-ish temp folder into the sandbox root
        scratch = make_folder(api, f"{PREFIX}scratch")
        created.append(scratch)
        res = organize._batch_move(api, [scratch], root_id)
        check("真实移动文件夹进沙箱成功", res[0] is True, str(res))
        names = {e.get("name") for e in api.list_folder(root_id)}
        check("移动后目录出现在沙箱内", f"{PREFIX}scratch" in names, f"{sorted(names)}")

        # 4b) moving into itself is correctly rejected (no crash)
        bad = organize._batch_move(api, [root_id], root_id)
        check("禁止移动到自身子目录（被安全拒绝）", bad[0] is False and "file_move" in str(bad[1]),
              str(bad))
    finally:
        print("  · 清理沙箱…")
        for fid in reversed(created):
            try:
                api.trash(fid)
            except Exception as exc:
                print(f"    ! 清理失败 {fid}: {exc}")
        time.sleep(1)
        root_names = {e.get("name") for e in api.list_folder("")}
        check("沙箱已从根目录移除", SANDBOX not in root_names)
        import shutil

        shutil.rmtree(home, ignore_errors=True)

    print(f"\n结果：{passed}/{passed + failed} 通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
