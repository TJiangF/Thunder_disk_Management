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
  [d] 打开数据目录
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


def _do_login(cfg) -> None:
    chrome_tokens.harvest(cfg, restart=True)


def _do_review(cfg) -> None:
    from . import __main__ as cli

    cli.cmd_review(_ns(port=_prompt_int("网页端口", 8765)), util.load_config())


def _require_login() -> bool:
    if _is_logged_in():
        return True
    util.log("还没有登录迅雷云盘，请先选 [1] 登录。", "WARN")
    return False


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
            cli.cmd_shots(_ns(top=100, more=None, all=False, ids=None, min_size=None,
                              no_resume=False, retry_failed=False, workers=None),
                          util.load_config())
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
    elif choice == "d":
        _open_dir(util.ROOT)
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
