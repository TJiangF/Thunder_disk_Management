"""Command line entry point: python -m thunder_sweeper <command>."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import threading
import time

from . import categories, chrome_tokens, classify, dedupe, organize, screenshots, thunder_api, util


def _check_deps() -> None:
    missing = [m for m in ("requests", "websocket") if importlib.util.find_spec(m) is None]
    if missing:
        print(
            "缺少依赖: " + ", ".join(missing) + "\n"
            "请用项目虚拟环境运行（推荐）：\n"
            "  ./sweeper <命令>\n"
            "或先激活环境并安装依赖：\n"
            "  source .venv/bin/activate\n"
            "  python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt",
            file=sys.stderr,
        )
        raise SystemExit(1)


def cmd_login(args, cfg):
    chrome_tokens.harvest(cfg, restart=getattr(args, "restart", False))


def _load_videos() -> list[dict]:
    videos = util.read_json(util.VIDEOS_FILE)
    if not videos:
        raise SystemExit("没有 videos.json，请先运行: python -m thunder_sweeper scan")
    return videos


def cmd_scan(args, cfg):
    provider = chrome_tokens.load_provider(cfg)
    api = thunder_api.ThunderAPI(provider, cfg)

    state: dict = {}
    if args.resume:
        loaded = util.read_json(util.SCAN_STATE_FILE)
        if loaded:
            state = loaded
            util.log(f"从上次进度恢复：已访问 {len(state.get('visited', []))} 个目录，"
                     f"已收集 {len(state.get('videos', []))} 个视频")

    last_save = {"t": 0.0}

    def on_progress(n_videos, n_dirs, path):
        now = time.time()
        if now - last_save["t"] > 5:
            util.atomic_write_json(util.SCAN_STATE_FILE, state)
            last_save["t"] = now
        if n_dirs % 20 == 0:
            util.log(f"已扫描 {n_dirs} 个目录，发现 {n_videos} 个视频 (当前: {path})")

    util.log("开始递归扫描网盘...")
    state = api.walk(state=state, on_progress=on_progress)
    util.atomic_write_json(util.SCAN_STATE_FILE, state)

    videos = sorted(state.get("videos", []), key=lambda v: v.get("size", 0), reverse=True)
    if not videos and not state.get("files"):
        failed = state.get("failed") or []
        if failed:
            raise SystemExit(
                f"扫描未获取到任何文件（失败目录 {len(failed)} 个，首个：{failed[0][1]}）。"
                "已保留原有数据，未覆盖。请检查网络后重试。"
            )
        raise SystemExit("扫描未获取到任何文件（云盘可能为空，或 token 失效）。已保留原有数据。")
    if args.min_size:
        floor = int(args.min_size * 1024 * 1024)
        videos = [v for v in videos if v.get("size", 0) >= floor]
    if args.limit:
        videos = videos[: args.limit]

    util.atomic_write_json(util.VIDEOS_FILE, videos)
    util.atomic_write_json(util.FILES_FILE, state.get("files", []))
    total = sum(v.get("size", 0) for v in videos)
    util.log(f"扫描完成：{len(videos)} 个视频，共 {util.human_size(total)}")
    util.log(f"全部文件：{len(state.get('files', []))} 个 → {util.FILES_FILE.name}")
    util.log(f"结果已保存: {util.VIDEOS_FILE}")
    for v in videos[:10]:
        util.log(f"  {util.human_size(v.get('size')):>10}  {v.get('name')}")


def _select_videos(videos: list[dict], args) -> list[dict]:
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
        return [v for v in videos if v["id"] in wanted]
    videos = sorted(videos, key=lambda v: v.get("size", 0), reverse=True)
    if args.min_size:
        floor = int(args.min_size * 1024 * 1024)
        videos = [v for v in videos if v.get("size", 0) >= floor]
    if args.all:
        return videos
    return videos[: args.top]


def _load_shots_state() -> dict:
    return util.read_json(util.SHOTS_STATE_FILE, {}) or {}


def _save_shots_state(state: dict) -> None:
    util.atomic_write_json(util.SHOTS_STATE_FILE, state)


def _record_shot(state: dict, video: dict) -> None:
    state[str(video["id"])] = {
        "done": bool(video.get("done")),
        "thumbs": len(video.get("thumbs") or []),
        "error": video.get("error"),
        "ts": int(time.time()),
    }


def _pending(videos: list[dict], needed: int, state: dict, retry_failed: bool) -> list[dict]:
    """Videos not yet fully screenshotted and not previously attempted."""
    out = []
    for v in videos:
        if screenshots.is_complete(v, needed):
            continue
        if not retry_failed and str(v["id"]) in state:
            continue
        out.append(v)
    return out


def cmd_shots(args, cfg):
    screenshots.ensure_tools()
    provider = chrome_tokens.load_provider(cfg)

    videos = _load_videos()
    needed = len(cfg["fractions"])
    state = _load_shots_state()
    retry_failed = bool(getattr(args, "retry_failed", False))
    state_lock = threading.Lock()

    def on_done(video):
        with state_lock:
            _record_shot(state, video)
            _save_shots_state(state)

    if getattr(args, "more", None) or getattr(args, "all", False):
        pool = _pending(videos, needed, state, retry_failed)
        if args.min_size:
            floor = int(args.min_size * 1024 * 1024)
            pool = [v for v in pool if (v.get("size") or 0) >= floor]
        if args.all:
            videos = pool
            util.log(f"全部截图：还剩 {len(pool)} 个未处理"
                     + ("（含之前失败/半截的）" if retry_failed else "（已跳过尝试过的）"))
        else:
            videos = pool[: args.more]
            util.log(f"继续截图：还剩 {len(pool)} 个未处理，本次处理 {len(videos)} 个")
    else:
        videos = _select_videos(videos, args)

    if not videos:
        raise SystemExit("没有匹配的视频（可能已全部处理；要重试失败的可加 --retry-failed）")
    requested = int(getattr(args, "workers", 0) or cfg.get("workers", 3))
    workers = util.cap_workers(requested)
    if workers != requested:
        util.log(f"并发上限为 CPU 线程数 {util.cpu_count()}，已从 {requested} 调整为 {workers}", "WARN")
    total = sum(v.get("size", 0) for v in videos)
    util.log(f"准备为 {len(videos)} 个视频生成截图（共 {util.human_size(total)}），并发 {workers}")

    counters = {"ok": 0, "fail": 0}
    use_live = sys.stderr.isatty()
    live = util.LiveDisplay(len(videos), slots=workers, label="截图",
                            console_lines=6) if use_live else None
    bar = None if use_live else util.ProgressBar(len(videos), label="")

    def progress(i, total_n, video):
        ok = bool(video.get("done"))
        counters["ok" if ok else "fail"] += 1
        if live is not None:
            live.set_progress(i, counters["ok"], counters["fail"])
        else:
            name = (video.get("name") or "")[:36]
            flag = "✓" if ok else "✗"
            bar.update(i, counters["ok"], counters["fail"], f"{flag} {name}")

    if live is not None:
        live.start()
    else:
        bar.start()
    try:
        screenshots.process_many(provider, videos, cfg, workers=workers,
                                 progress=progress, on_done=on_done, live=live)
    except KeyboardInterrupt:
        if live is not None:
            live.finish(label="已中断")
        elif bar._tty:
            bar.stream.write("\n")
        util.atomic_write_json(util.QUEUE_FILE, videos)
        util.log("已中断，进度已保存", "WARN")
        raise
    finally:
        util.atomic_write_json(util.QUEUE_FILE, videos)
    if live is not None:
        live.finish()
    else:
        bar.finish()

    failed = [v for v in videos if not v.get("done")]
    done = len(videos) - len(failed)
    util.log(f"截图完成：{done}/{len(videos)} 个成功，结果: {util.QUEUE_FILE}")
    for v in failed[:10]:
        util.log(f"  ✗ {v.get('name')}  {v.get('error') or ''}", "WARN")
    if len(failed) > 10:
        util.log(f"  …另有 {len(failed) - 10} 个未成功", "WARN")


def cmd_review(args, cfg):
    videos = _load_videos()

    classified = util.read_json(util.CLASSIFIED_FILE) or []
    recs = {c["id"]: c for c in classified}
    manual = classify.load_manual()
    needed = len(cfg["fractions"])

    for v in videos:
        cid = v["id"]
        rec = recs.get(cid) or {}
        auto = rec.get("auto_category")
        if not auto:
            auto, _ = classify.classify(v.get("name") or "", v.get("path") or "")
        v["auto_category"] = auto
        v["category"] = manual.get(cid, rec.get("category", auto))
        v["manual"] = cid in manual
        v["thumbs"] = screenshots.disk_thumbs(cid, needed)

    with_thumbs = sum(1 for v in videos if v["thumbs"])
    util.log(f"共 {len(videos)} 个视频（其中 {with_thumbs} 个有截图）")
    if not classified:
        util.log("尚未分类，建议先运行: ./sweeper classify", "WARN")

    play_url_fn = None
    download_url_fn = None
    api = None
    try:
        provider = chrome_tokens.load_provider(cfg)
        api = thunder_api.ThunderAPI(provider, cfg)
        preference = cfg.get("link_preference", "media")

        def play_url_fn(file_id):
            url, _ = api.play_url(file_id, preference=preference)
            return url

        def download_url_fn(file_id):
            # prefer the VIP accelerated link for downloading
            url, _ = api.play_url(file_id, preference="vip")
            return url
    except Exception as exc:
        util.log(f"播放链接不可用（{exc}），review 仍可继续", "WARN")

    shots_fn = None
    if api is not None:
        def shots_fn(mode, count, workers, on_progress):
            needed = len(cfg["fractions"])
            state = _load_shots_state()
            state_lock = threading.Lock()

            def on_done(video):
                with state_lock:
                    _record_shot(state, video)
                    _save_shots_state(state)

            pool = _pending(videos, needed, state, False)
            selected = pool if mode == "all" else pool[: max(1, int(count))]
            requested = int(workers or cfg.get("workers", 3))
            workers = util.cap_workers(requested)
            if workers != requested:
                util.log(f"并发上限为 CPU 线程数 {util.cpu_count()}，已从 {requested} 调整为 {workers}", "WARN")
            util.log(f"网页截图任务：{len(selected)} 个，并发 {workers}")
            screenshots.process_many(provider, selected, cfg, workers=workers,
                                     progress=on_progress, on_done=on_done)
            util.atomic_write_json(util.QUEUE_FILE, [v for v in videos if v.get("thumbs")])
            return selected

    apply_fn = None
    if api is not None:
        def apply_fn(ids, on_progress):
            results = []
            total = len(ids)
            for i, fid in enumerate(ids, 1):
                try:
                    api.trash(fid)
                    results.append({"id": fid, "ok": True, "error": None})
                except Exception as exc:
                    results.append({"id": fid, "ok": False, "error": str(exc)})
                on_progress(i, total, fid)
                time.sleep(cfg["api_delay"])
            ok = sum(1 for r in results if r["ok"])
            fail = len(results) - ok
            util.atomic_write_json(util.DATA_DIR / "applied.json",
                                   {"applied_at": int(time.time()), "ok": ok, "fail": fail})
            util.log(f"网页执行删除完成：成功 {ok}，失败 {fail}")
            return results

    organize_fn = None
    if api is not None:
        def organize_fn(opts, on_progress):
            exclude = set(categories.RESERVED)
            if not opts.get("include_other"):
                exclude.add("adult_other")
            move_cats = categories.ids() - exclude
            plan = organize.load_and_build(cfg, move_cats=move_cats,
                                           fix_inside=bool(opts.get("fix_inside")))
            util.atomic_write_json(util.DATA_DIR / "organize_plan.json", plan)
            result = {}
            if opts.get("apply"):
                def prog(i, total, to_path):
                    on_progress("移动", i, total, "→ " + str(to_path))
                result["move"] = organize.apply_plan(
                    api, plan, limit=opts.get("limit"),
                    delete_folders=bool(opts.get("delete_folders")),
                    progress=prog, log=util.log,
                    chunk=cfg.get("organize_chunk", 8), delay=cfg.get("organize_delay", 1.0))
                util.atomic_write_json(util.DATA_DIR / "organize_applied.json", result["move"])
            if opts.get("clean_junk"):
                on_progress("清理", 0, 0, "清理垃圾文件夹…")
                result["clean"] = organize.clean_junk_folders(api, log=util.log)
                util.atomic_write_json(util.DATA_DIR / "organize_clean.json", result["clean"])
            if not opts.get("no_rescan"):
                _auto_rescan_classify(cfg, api, progress=on_progress)
            return result

    selections = review_server_serve(videos, args.port, play_url_fn, shots_fn, apply_fn,
                                     download_url_fn, organize_fn)
    util.log(f"已记录 {len(selections.get('delete', []))} 个待删文件: {util.SELECTIONS_FILE}")


def review_server_serve(queue, port, play_url_fn=None, shots_fn=None, apply_fn=None,
                        download_url_fn=None, organize_fn=None):
    from . import review_server

    return review_server.serve(queue, port=port, open_browser=True,
                               play_url_fn=play_url_fn, shots_fn=shots_fn, apply_fn=apply_fn,
                               download_url_fn=download_url_fn, organize_fn=organize_fn)


def cmd_apply(args, cfg):
    selections = util.read_json(util.SELECTIONS_FILE)
    if not selections:
        raise SystemExit("没有 selections.json，请先运行 review 并提交选择")
    items = selections.get("items") or [{"id": i} for i in selections.get("delete", [])]
    if not items:
        util.log("没有需要删除的视频")
        return

    total = sum((it.get("size") or 0) for it in items)
    util.log(f"将把 {len(items)} 个视频移入回收站，共 {util.human_size(total)}")
    for it in items:
        util.log(f"  - {it.get('name')} ({util.human_size(it.get('size'))})")

    if args.dry_run:
        util.log("dry-run：未执行任何删除", "WARN")
        return
    if not args.yes:
        answer = input("确认执行？输入 yes 继续: ").strip().lower()
        if answer != "yes":
            util.log("已取消", "WARN")
            return

    provider = chrome_tokens.load_provider(cfg)
    api = thunder_api.ThunderAPI(provider, cfg)

    ok, fail = 0, 0
    for it in items:
        fid = it.get("id")
        name = it.get("name")
        try:
            api.trash(fid)
            ok += 1
            util.log(f"已移入回收站: {name}")
        except Exception as exc:
            fail += 1
            util.log(f"删除失败 {name}: {exc}", "ERROR")
        time.sleep(cfg["api_delay"])

    util.log(f"完成：成功 {ok}，失败 {fail}")
    util.atomic_write_json(util.DATA_DIR / "applied.json",
                           {"applied_at": int(time.time()), "ok": ok, "fail": fail})


def cmd_inspect(args, cfg):
    provider = chrome_tokens.load_provider(cfg)
    api = thunder_api.ThunderAPI(provider, cfg)

    target = args.target
    if target.isdigit():
        videos = _load_videos()
        idx = int(target)
        if idx < 1 or idx > len(videos):
            raise SystemExit(f"序号超出范围：1..{len(videos)}")
        video = videos[idx - 1]
        util.log(f"第 {idx} 个（按大小降序）：{video.get('name')}")
        file_id = video["id"]
    else:
        file_id = target

    info = api.file_info(file_id)
    print(json.dumps(info, ensure_ascii=False, indent=2))

    links = info.get("links") or {}
    print("\n--- 可用地址 ---")
    candidates = {}
    for key, value in links.items():
        url = value.get("url") if isinstance(value, dict) else None
        if url:
            candidates[key] = url
            print(f"{key}: {url[:120]}...")
    medias = info.get("medias") or []
    if medias and isinstance(medias[0], dict):
        media_link = medias[0].get("link") or {}
        if isinstance(media_link, dict) and media_link.get("url"):
            candidates["medias[0].link"] = media_link["url"]
            print(f"medias[0].link: {media_link['url'][:120]}...")
    if info.get("web_content_link"):
        candidates["web_content_link"] = info["web_content_link"]

    print("\n--- Range / 加速测试（决定能否精确定位截图）---")
    for name, url in candidates.items():
        print(f"{name}: {_check_range(url)}")
    print("\n（提示：以上链接含签名，属于敏感信息，勿外传）")


def _check_range(url: str) -> dict:
    import requests as _requests
    try:
        resp = _requests.get(
            url,
            headers={
                "Range": "bytes=0-1023",
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://pan.xunlei.com/",
            },
            timeout=20,
            stream=True,
        )
        result = {
            "status": resp.status_code,
            "accept_ranges": resp.headers.get("Accept-Ranges"),
            "content_range": resp.headers.get("Content-Range"),
            "content_length": resp.headers.get("Content-Length"),
        }
        resp.close()
        return result
    except Exception as exc:
        return {"error": str(exc)}


def cmd_classify(args, cfg):
    source = util.FILES_FILE if util.FILES_FILE.exists() else util.VIDEOS_FILE
    if not source.exists():
        raise SystemExit("没有可分类的清单，请先运行: ./sweeper scan")
    items = util.read_json(source) or []
    if not items:
        raise SystemExit(f"{source.name} 为空")

    classified = classify.categorize_files(items)
    order = {"jp": 0, "west": 1, "cn": 2, "adult_other": 3, "non_adult": 4, "unknown": 5}
    classified.sort(key=lambda x: (order.get(x.get("category"), 9), -int(x.get("size") or 0)))
    util.atomic_write_json(util.CLASSIFIED_FILE, classified)

    stats = classify.summarize(classified)
    total = sum(s["count"] for s in stats.values())
    util.log(f"来源 {source.name}，共 {total} 个文件，分类结果：")
    for cat in ("jp", "west", "cn", "adult_other", "non_adult", "unknown"):
        s = stats.get(cat)
        if s:
            util.log(f"  {classify.LABELS[cat]:<8} {s['count']:>6} 个   {util.human_size(s['bytes'])}")
    util.log(f"已保存: {util.CLASSIFIED_FILE}")


def cmd_dedupe(args, cfg):
    videos = _load_videos()
    classified = util.read_json(util.CLASSIFIED_FILE) or []
    cats = {c["id"]: c.get("category", "unknown") for c in classified}
    for v in videos:
        v["category"] = cats.get(v["id"], "unknown")
    groups = dedupe.find_duplicates(videos)
    payload = dedupe.group_payload(groups)
    util.atomic_write_json(util.DATA_DIR / "duplicates.json", payload)
    waste = sum(g["wasted"] for g in payload)
    util.log(f"发现 {len(payload)} 组重复，可省 {util.human_size(waste)}")
    for g in payload[:15]:
        util.log(f"  {g['count']} 个 · 单个 {util.human_size(g['size'])} · 番号 {g['code'] or '—'} · 可省 {util.human_size(g['wasted'])}")
    util.log("结果: data/duplicates.json；网页“重复去重”里可勾选删除")


def _auto_rescan_classify(cfg, api, progress=None):
    """Full re-scan + re-classify (used after organize apply/clean)."""
    def scan_prog(n_videos, n_dirs, path):
        if progress:
            progress("扫描", n_dirs, 0, f"已扫描 {n_dirs} 个目录 / {n_videos} 个视频")

    state = api.walk(state={}, on_progress=scan_prog)
    videos = sorted(state.get("videos", []), key=lambda v: v.get("size", 0), reverse=True)
    files = state.get("files", [])
    if not files and (util.read_json(util.FILES_FILE) or []):
        raise RuntimeError(
            "重新扫描没有取到任何文件，已保留原有 videos/files/classified，未覆盖。"
            "请检查网络后重试。"
        )
    util.atomic_write_json(util.VIDEOS_FILE, videos)
    util.atomic_write_json(util.FILES_FILE, files)
    util.atomic_write_json(util.SCAN_STATE_FILE, state)

    if progress:
        progress("分类", 0, 1, "重新分类…")
    classified = classify.categorize_files(files)
    util.atomic_write_json(util.CLASSIFIED_FILE, classified)
    if progress:
        progress("分类", 1, 1, "分类完成")
    util.log(f"已重新扫描并分类：{len(videos)} 个视频 / {len(files)} 个文件")
    return files, videos


def cmd_organize(args, cfg):
    exclude = set(categories.RESERVED)
    if not getattr(args, "include_other", False):
        exclude.add("adult_other")
    move_cats = categories.ids() - exclude
    plan = organize.load_and_build(cfg, move_cats=move_cats, fix_inside=getattr(args, "fix_inside", False))
    util.atomic_write_json(util.DATA_DIR / "organize_plan.json", plan)
    s = plan["summary"]
    util.log(f"数据来源 {plan['files_source']} · 目标根 {plan['base']}")
    util.log(f"待移动 {s['move_count']} 个，共 {util.human_size(s['move_size'])}")
    for k, v in s["targets"].items():
        util.log(f"  → {k}: {v} 个")
    util.log(f"将删除文件夹 {s['delete_folder_count']} 个"
             f"（顺带清理小文件/种子 {s['delete_extra_count']} 个 / {util.human_size(s['delete_extra_size'])}）")
    util.log(f"保留文件夹 {s['keep_folder_count']} 个（含较大的其他文件）")
    util.log(f"路径与分类不一致 {s.get('mismatch_count', 0)} 个 / {util.human_size(s.get('mismatch_size', 0))}"
             "（勾选“纠正”或加 --fix-inside 才会移动）")

    if not args.apply and not args.clean_junk:
        util.log("这是预览。确认无误后加 --apply 执行；测试可加 --limit N 只移动前 N 个")
        util.log("清理只含垃圾文件的文件夹：加 --clean-junk")
        util.log("明细: data/organize_plan.json（网页“整理”页可预览）")
        return

    limit = args.limit
    if not args.yes:
        what = []
        if args.apply:
            what.append(f"移动前 {limit} 个" if limit else f"移动 {s['move_count']} 个")
            if args.delete_folders:
                what.append(f"删除 {s['delete_folder_count']} 个源文件夹")
        if args.clean_junk:
            what.append("清理所有只含垃圾文件的文件夹")
        answer = input("确认执行：" + "，".join(what) + "？输入 yes 继续: ").strip().lower()
        if answer != "yes":
            util.log("已取消", "WARN")
            return

    provider = chrome_tokens.load_provider(cfg)
    api = thunder_api.ThunderAPI(provider, cfg)

    def progress(i, total, to_path):
        util.log(f"移动中 {i}/{total} → {to_path}")

    if args.apply:
        result = organize.apply_plan(api, plan, limit=limit,
                                     delete_folders=args.delete_folders, progress=progress,
                                     log=util.log,
                                     chunk=cfg.get("organize_chunk", 8),
                                     delay=cfg.get("organize_delay", 1.0))
        util.atomic_write_json(util.DATA_DIR / "organize_applied.json", result)
        rs = result["summary"]
        util.log(f"移动完成：成功 {rs['moved_ok']}，失败 {rs['moved_fail']}，跳过(已在目标) {rs['skipped_already']}"
                 + (f"，删除文件夹 {len(rs['deleted_folders'])}" if rs["deleted_folders"] else ""))
        util.log("明细: data/organize_applied.json")

    if args.clean_junk:
        util.log("开始清理只含垃圾文件的文件夹（递归）…")
        res = organize.clean_junk_folders(api, log=util.log)
        util.atomic_write_json(util.DATA_DIR / "organize_clean.json", res)
        util.log(f"清理完成：删除文件夹 {len(res['deleted'])} 个，失败 {len(res['failed'])}")
        util.log("明细: data/organize_clean.json")

    if (args.apply or args.clean_junk) and not getattr(args, "no_rescan", False):
        util.log("开始自动重新扫描并分类…")
        _auto_rescan_classify(cfg, api, progress=lambda ph, c, t, m: util.log(f"[{ph}] {m}"))


def cmd_status(args, cfg):
    videos = util.read_json(util.VIDEOS_FILE) or []
    queue = util.read_json(util.QUEUE_FILE) or []
    sel = util.read_json(util.SELECTIONS_FILE) or {}
    tokens = util.read_json(util.TOKENS_FILE) or {}
    util.log(f"tokens.json : {'已登录' if tokens.get('credentials.access_token') else '未登录'}")
    util.log(f"videos.json : {len(videos)} 个视频")
    util.log(f"queue.json  : {len(queue)} 个视频（已生成截图）")
    util.log(f"待删除      : {len(sel.get('delete', []))} 个")


def _normalize_argv(argv: list[str]) -> list[str]:
    """Allow shorthand: ``-100`` -> ``--more 100``, ``-all`` -> ``--all``."""
    out: list[str] = []
    for arg in argv:
        if re.fullmatch(r"-\d+", arg):
            out += ["--more", arg[1:]]
        elif arg == "-all":
            out.append("--all")
        else:
            out.append(arg)
    return out


def main(argv=None):
    _check_deps()
    util.ensure_dirs()
    parser = argparse.ArgumentParser(
        prog="thunder_sweeper",
        description="迅雷云盘视频清理：按大小收集 -> 云播直链截图 -> 本地勾选 -> 移入回收站",
    )
    parser.add_argument("--version", action="version",
                        version=f"ThunderSweeper {util.app_version()}")
    sub = parser.add_subparsers(dest="command", required=False)

    p_login = sub.add_parser("login", help="启动调试 Chrome 并抓取迅雷 token")
    p_login.add_argument("--restart", action="store_true", help="先关闭旧的调试 Chrome 再重新启动")
    p_status = sub.add_parser("status", help="查看当前状态")

    p_inspect = sub.add_parser("inspect", help="打印某视频的 file_info（排查直链用）")
    p_inspect.add_argument("target", help="videos.json 中的序号（从 1 开始，按大小降序）或文件 id")

    sub.add_parser("classify", help="按命名规则分类：日本/欧美/国产/非成人/未知")

    sub.add_parser("dedupe", help="扫描重复文件（同大小+名称相似/同番号），写 data/duplicates.json")

    p_org = sub.add_parser("organize", help="整理方案：预览；--apply 执行移动/删除文件夹")
    p_org.add_argument("--apply", action="store_true", help="执行移动（默认只预览）")
    p_org.add_argument("--limit", type=int, metavar="N", help="只移动前 N 个（测试用；此时不删文件夹）")
    p_org.add_argument("--delete-folders", action="store_true", help="连同删除空的/只剩垃圾的源文件夹")
    p_org.add_argument("--clean-junk", action="store_true",
                       help="递归删除只含垃圾文件（apk/html/txt/图片/种子…）或为空的文件夹")
    p_org.add_argument("--include-other", action="store_true",
                       help="连“成人-其他”也一起移动（默认只移 日本/欧美/国产）")
    p_org.add_argument("--no-rescan", action="store_true",
                       help="执行后不自动重新扫描+分类（默认会自动做）")
    p_org.add_argument("--fix-inside", action="store_true",
                       help="连同纠正“已在 /整理 内但归类不对”的文件（默认不动它们）")
    p_org.add_argument("--yes", action="store_true", help="跳过确认")

    p_scan = sub.add_parser("scan", help="递归扫描整个网盘，收集视频并按大小排序")
    p_scan.add_argument("--resume", action="store_true", help="从上次扫描进度继续")
    p_scan.add_argument("--limit", type=int, help="只保留最大的 N 个")
    p_scan.add_argument("--min-size", type=float, metavar="MB", help="只保留大于该大小的视频(MB)")

    p_shots = sub.add_parser("shots", help="为视频生成 8 张截图（云播直链 + ffmpeg）")
    p_shots.add_argument("--top", type=int, default=10, help="处理最大的 N 个（默认10）")
    p_shots.add_argument("--more", type=int, metavar="N", help="在当前已完成的基础上，继续截图 N 个（可用 -N 简写）")
    p_shots.add_argument("--all", action="store_true", help="处理全部视频")
    p_shots.add_argument("--ids", help="只处理指定 id，逗号分隔")
    p_shots.add_argument("--min-size", type=float, metavar="MB", help="过滤小于该大小的视频")
    p_shots.add_argument("--no-resume", action="store_true", help="不跳过已生成的截图")
    p_shots.add_argument("--retry-failed", action="store_true",
                         help="重试之前失败/半截的视频（默认 --more/--all 会跳过它们）")
    p_shots.add_argument("--workers", type=int, help="并发线程数（默认取 config.workers，通常 3）")

    p_review = sub.add_parser("review", help="打开本地管家页面（框图/筛选/审核）")
    p_review.add_argument("--port", type=int, default=8765)

    p_apply = sub.add_parser("apply", help="执行删除（移入回收站）")
    p_apply.add_argument("--yes", action="store_true", help="跳过确认")
    p_apply.add_argument("--dry-run", action="store_true", help="只演示不执行")

    sub.add_parser("wizard", help="交互式菜单（不带参数运行时自动进入）")
    p_self = sub.add_parser("selftest", help="运行内置自检（--live 会真实调用迅雷接口做沙箱测试）")
    p_self.add_argument("--live", action="store_true", help="包含真实 API 沙箱测试（需已登录）")

    args = parser.parse_args(_normalize_argv(argv if argv is not None else sys.argv[1:]))
    cfg = util.load_config()

    handlers = {
        "login": cmd_login,
        "scan": cmd_scan,
        "shots": cmd_shots,
        "review": cmd_review,
        "apply": cmd_apply,
        "status": cmd_status,
        "inspect": cmd_inspect,
        "classify": cmd_classify,
        "dedupe": cmd_dedupe,
        "organize": cmd_organize,
    }
    if args.command == "selftest":
        from . import selftest

        return selftest.run(live=args.live)
    if args.command in (None, "wizard"):
        from . import wizard

        if args.command is None and not sys.stdin.isatty():
            parser.print_help()
            return 0
        return wizard.run(cfg)
    try:
        handlers[args.command](args, cfg)
    except KeyboardInterrupt:
        util.log("用户中断", "WARN")
        return 130
    except (chrome_tokens.TokenError, screenshots.ToolMissing) as exc:
        util.log(str(exc), "ERROR")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
