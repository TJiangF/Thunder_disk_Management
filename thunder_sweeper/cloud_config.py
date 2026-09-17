"""Pull a user settings *bundle* from the cloud ``/config`` folder at startup.

A bundle is one JSON file that holds all user-authored settings:

* ``manual_categories`` — manual category overrides
* ``ratings``           — 5-star ratings
* ``categories``        — the category tree
* ``classify_rules``    — keyword / override rules

On each launch we pull the cloud copy, compare its update time with the local
bundle and let the user pick which one to load.  Nothing is ever uploaded here.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from . import util

CLOUD_DIR = "config"                    # folder name at the cloud drive root
CLOUD_FILE = "sweeper_config.json"
LOCAL_FILE = "sweeper_config.json"      # inside the local data dir

_PARTS = [
    ("manual_categories", "MANUAL_CATS_FILE"),
    ("ratings", "RATINGS_FILE"),
    ("categories", "CATEGORIES_FILE"),
    ("classify_rules", "CLASSIFY_RULES_FILE"),
]


def local_path() -> Path:
    return util.DATA_DIR / LOCAL_FILE


def make_bundle() -> dict:
    bundle = {"version": 1, "app": util.APP_NAME, "updated_at": int(time.time())}
    for key, attr in _PARTS:
        path = getattr(util, attr)
        if path.exists():
            bundle[key] = util.read_json(path, {}) or {}
    return bundle


def save_local_bundle() -> Path:
    path = local_path()
    util.atomic_write_json(path, make_bundle())
    return path


def apply_bundle(bundle: dict) -> list[str]:
    """Write the bundle's parts back to the working data files."""
    written = []
    for key, attr in _PARTS:
        if key not in bundle:
            continue
        util.atomic_write_json(getattr(util, attr), bundle[key])
        written.append(key)
    return written


def local_mtime() -> float | None:
    path = local_path()
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _parse_ts(value) -> float | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _cloud_mtime(info: dict) -> float | None:
    for key in ("modified_time", "user_modified_time", "created_time",
                "original_create_time"):
        ts = _parse_ts(info.get(key))
        if ts:
            return ts
    return None


def find_cloud_file(api) -> dict | None:
    """The ``/config/sweeper_config.json`` entry, if it exists."""
    try:
        root = api.list_folder("")
    except Exception:
        return None
    folder = next((e for e in root
                   if e.get("kind") == "drive#folder" and e.get("name") == CLOUD_DIR), None)
    if not folder:
        return None
    try:
        entries = api.list_folder(folder["id"])
    except Exception:
        return None
    return next((e for e in entries
                 if e.get("kind") == "drive#file" and e.get("name") == CLOUD_FILE), None)


def download(api, file_id: str) -> bytes:
    from .chrome_tokens import UA
    import requests

    links = api.play_links(file_id)
    url = links.get("vip") or links.get("media") or links.get("web")
    if not url:
        raise RuntimeError("云端文件没有可用下载地址")
    resp = requests.get(url, headers={"User-Agent": UA,
                                      "Referer": "https://pan.xunlei.com/"},
                        timeout=30)
    resp.raise_for_status()
    return resp.content


def pull(api) -> dict | None:
    """Fetch the cloud bundle; returns {bytes, mtime, info} or None."""
    entry = find_cloud_file(api)
    if not entry:
        return None
    info = {}
    try:
        info = api.file_info(entry["id"]) or {}
    except Exception:
        pass
    data = download(api, entry["id"])
    mtime = _cloud_mtime(info) or _parse_ts(entry.get("modified_time"))
    return {"bytes": data, "mtime": mtime, "info": info, "entry": entry}


def _fmt_ts(mtime: float | None) -> str:
    if not mtime:
        return "（无）"
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")


def sync(api, cfg: dict, choice: str | None = None, interactive: bool = True) -> str:
    """Compare cloud vs local bundle and load the chosen one.

    ``choice`` may be ``"cloud"`` / ``"local"`` to skip the prompt.
    Returns ``"cloud"`` | ``"local"`` | ``"none"``.
    """
    remote = pull(api)
    if remote is None:
        util.log(f"云端没有 /{CLOUD_DIR}/{CLOUD_FILE}，跳过同步")
        return "none"

    try:
        bundle = json.loads(remote["bytes"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        util.log(f"云端配置无法解析（{exc}），跳过", "WARN")
        return "none"

    cloud_ts = remote["mtime"]
    local_ts = local_mtime()
    util.log(f"云端配置更新时间: {_fmt_ts(cloud_ts)}")
    util.log(f"本地配置更新时间: {_fmt_ts(local_ts)}")

    if choice not in ("cloud", "local"):
        if not interactive or not sys.stdin.isatty():
            choice = "cloud" if (local_ts is None or (cloud_ts and cloud_ts > local_ts)) else "local"
        else:
            default = "1" if (local_ts is None or (cloud_ts and cloud_ts > local_ts)) else "2"
            print("  [1] 用云端覆盖本地   [2] 保留本地   [3] 跳过本次")
            raw = input(f"  请选择（回车={default}）: ").strip() or default
            choice = {"1": "cloud", "2": "local", "3": "none"}.get(raw, "local")

    if choice == "cloud":
        local_path().parent.mkdir(parents=True, exist_ok=True)
        local_path().write_bytes(remote["bytes"])
        written = apply_bundle(bundle)
        util.log(f"已用云端配置覆盖本地（{', '.join(written) or '无字段'}）")
        return "cloud"
    if choice == "local":
        if not local_path().exists():
            save_local_bundle()
        util.log("保留本地配置")
        return "local"
    util.log("已跳过同步")
    return "none"
