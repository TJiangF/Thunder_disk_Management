"""Simple interactive menu for non-technical users.

Running ``thunder-sweeper`` with no arguments on a terminal drops into this
wizard, which wraps the individual commands behind a numbered menu.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from . import chrome_tokens, util

_MENU = """
┌───────────────────────────────────────────────────────────────┐
│  ThunderSweeper — 迅雷云盘整理助手  v{ver:<8}                │
└───────────────────────────────────────────────────────────────┘
  数据目录: {data}
  登录状态: {login}

  [1] 登录迅雷云盘          （首次使用必须先做）
  [2] 扫描云盘              （收集全部文件与视频）
  [3] 自动分类              （日本/欧美/国产/非成人…）
  [4] 生成截图              （可选，网页审核用）
  [5] 打开管理网页          （框图浏览 / 去重 / 审核 / 整理 / 删除）
  [6] 整理方案              （预览）
  [7] 执行整理              （移动 + 清理，会自动重扫）
  [8] 删除已勾选文件        （移入回收站）
  [9] 查看状态
  [s] 同步配置 (Sync Config)
  [d] 打开数据目录
  [h] 设置数据目录
  [q] 退出
"""


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


def _is_logged_in() -> bool:
    tokens = util.read_json(util.TOKENS_FILE) or {}
    return bool(tokens.get("credentials.access_token"))


def _pause() -> None:
    try:
        input("\n按回车返回菜单…")
    except (EOFError, KeyboardInterrupt):
        pass


def _prompt_int(question: str, default: int) -> int:
    raw = input(f"{question}（默认 {default}）: ").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _open_dir(path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        elif sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
        util.log(f"已打开: {path}")
    except Exception as exc:
        util.log(f"打开目录失败: {exc}", "WARN")


def _do_set_home() -> None:
    """Show the data directory and let the user point it somewhere else."""
    print("\n数据目录设置（存放 tokens/videos/截图/分类设置等）")
    print(f"  当前: {util.ROOT}")
    print(f"  默认: {util.DEFAULT_HOME}")
    raw = input("  输入新的数据目录（回车=取消，输入 default 恢复默认）: ").strip()
    if not raw:
        util.log("已取消")
    elif raw.lower() in ("default", "默认"):
        util.log(f"已恢复默认数据目录: {util.set_home(None)}")
    else:
        util.log(f"已设置数据目录: {util.set_home(raw)}")
    util.log("设置已保存，新启动的程序都会使用该目录。")


def _do_login(cfg) -> None:
    chrome_tokens.harvest(cfg, restart=True)


def _do_review(cfg) -> None:
    from . import __main__ as cli

    cli.cmd_review(_ns(port=_prompt_int("网页端口", 8765)), util.load_config())


def _do_shots(cfg) -> None:
    """Ask how many *additional* videos to screenshot and how many run in parallel."""
    from . import __main__ as cli
    from . import screenshots

    cpu = util.cpu_count()
    videos = util.read_json(util.VIDEOS_FILE) or []
    needed = len(cfg.get("fractions") or [])
    done = sum(1 for v in videos if screenshots.disk_thumbs(v.get("id"), needed))
    print(f"\n生成截图（每个视频 8 张，用于网页审核）")
    print(f"  当前已截图 {done} 个 / 共 {len(videos)} 个视频")
    print(f"  本机 CPU 线程数：{cpu}（并发可超过它，按需设置）")
    raw = input("  本次新增截图数量（数字，或输入 all 截全部；回车默认 50）: ").strip().lower()
    if raw in ("all", "a", "-all", "全部", "所有", "all!"):
        count, all_mode = None, True
    else:
        all_mode = False
        try:
            count = int(raw) if raw else 50
        except ValueError:
            count = 50
        if count < 1:
            count = 1
    default_workers = max(1, int(cfg.get("workers", 3) or 3))
    workers = util.prompt_int("  并发数量", default_workers, minimum=1)
    if all_mode:
        print(f"  → 截图全部剩余视频，并发 {workers}\n")
    else:
        print(f"  → 在已完成 {done} 个的基础上，再截图 {count} 个，并发 {workers}\n")
    cli.cmd_shots(_ns(top=count or 10, more=None if all_mode else count, all=all_mode,
                      ids=None, min_size=None, no_resume=False, retry_failed=False,
                      workers=workers),
                  util.load_config())


def _require_login() -> bool:
    if _is_logged_in():
        return True
    util.log("还没有登录迅雷云盘，请先选 [1] 登录。", "WARN")
    return False


def _do_sync_menu(cfg) -> None:
    """Sync Config submenu: Upload / Sync / Exit."""
    from . import cloud_config, thunder_api

    if not _require_login():
        return
    api = thunder_api.ThunderAPI(chrome_tokens.load_provider(cfg), cfg)
    while True:
        print("\n同步配置 (Sync Config)")
        print(f"  本地文件: {cloud_config.local_path()}")
        print(f"  云端文件: /{cloud_config.CLOUD_DIR}/{cloud_config.CLOUD_FILE}")
        print("  [1] Upload  上传：用本地覆盖云端存档（云端无则新建）")
        print("  [2] Sync    同步：用云端覆盖本地存档")
        print("  [3] Exit    返回上一级")
        try:
            choice = input("  请选择: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice in ("3", "q", "exit", ""):
            return
        try:
            if choice == "1":
                cloud_config.upload(api)
            elif choice == "2":
                cloud_config.sync(api)
            else:
                util.log("无效选项", "WARN")
        except Exception as exc:
            util.log(f"操作失败: {exc}", "ERROR")


def _menu_once(cfg) -> bool:
    choice = input("\n请选择操作: ").strip().lower()
    from . import __main__ as cli

    if choice in ("q", "quit", "exit"):
        return False
    if choice == "1":
        _do_login(cfg)
    elif choice == "2":
        if _require_login():
            cli.cmd_scan(_ns(resume=True, limit=None, min_size=None), util.load_config())
    elif choice == "3":
        cli.cmd_classify(_ns(), util.load_config())
    elif choice == "4":
        if _require_login():
            _do_shots(cfg)
    elif choice == "5":
        _do_review(cfg)
    elif choice == "6":
        cli.cmd_organize(_ns(apply=False, limit=None, delete_folders=False,
                             clean_junk=False, include_other=False, no_rescan=True,
                             fix_inside=False, yes=True), util.load_config())
    elif choice == "7":
        if not _require_login():
            return True
        answer = input("执行整理会把文件移动到 /整理 并按分类归位，确认？(yes/no): ").strip().lower()
        if answer == "yes":
            cli.cmd_organize(_ns(apply=True, limit=None, delete_folders=True,
                                 clean_junk=True, include_other=False, no_rescan=False,
                                 fix_inside=True, yes=True), util.load_config())
    elif choice == "8":
        if _require_login():
            cli.cmd_apply(_ns(yes=False, dry_run=False), util.load_config())
    elif choice == "9":
        cli.cmd_status(_ns(), util.load_config())
    elif choice == "s":
        _do_sync_menu(cfg)
    elif choice == "d":
        _open_dir(util.ROOT)
    elif choice == "h":
        _do_set_home()
    else:
        util.log("无效选项", "WARN")
    return True


def run(cfg: dict | None = None) -> int:
    cfg = cfg or util.load_config()
    util.ensure_dirs()
    while True:
        login = "已登录 ✓" if _is_logged_in() else "未登录（请先选 1）"
        print(_MENU.format(ver=util.app_version(), data=util.data_dir_display(), login=login))
        try:
            if not _menu_once(cfg):
                return 0
        except KeyboardInterrupt:
            print()
            return 130
        except SystemExit as exc:
            util.log(str(exc) or "操作未完成（请检查上面的错误信息）", "ERROR")
        except Exception as exc:
            util.log(f"操作失败: {exc}", "ERROR")
        _pause()
