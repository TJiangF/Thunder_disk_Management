"""Local settings *bundle* + cloud ``/config`` sync (pull and push).

A bundle is one JSON file holding all user-authored settings:

* ``manual_categories`` — manual category overrides
* ``ratings``           — 5-star ratings
* ``categories``        — the category tree
* ``classify_rules``    — keyword / override rules

* ``upload`` writes the local bundle to ``/config/sweeper_config.json`` (creating
  the ``config`` folder if needed, replacing any old copy).
* ``sync``  pulls the cloud copy and, after showing both timestamps, lets the
  user overwrite the local bundle with it.
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
    data = json.dumps(make_bundle(), ensure_ascii=False, indent=2).encode("utf-8")
    path = local_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def apply_bundle(bundle: dict) -> list[str]:
    written = []
    for key, attr in _PARTS:
        if key in bundle:
            util.atomic_write_json(getattr(util, attr), bundle[key])
            written.append(key)
    return written


def local_mtime() -> float | None:
    try:
        return local_path().stat().st_mtime
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


def _cloud_mtime_from_info(info: dict) -> float | None:
    for key in ("modified_time", "user_modified_time", "created_time",
                "original_create_time"):
        ts = _parse_ts(info.get(key))
        if ts:
            return ts
    return None


def _fmt_ts(mtime: float | None) -> str:
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S") if mtime else "暂无"


def find_cloud_dir(api) -> dict | None:
    try:
        root = api.list_folder("")
    except Exception:
        return None
    return next((e for e in root if e.get("kind") == "drive#folder"
                 and e.get("name") == CLOUD_DIR), None)


def find_cloud_file(api) -> dict | None:
    folder = find_cloud_dir(api)
    if not folder:
        return None
    try:
        entries = api.list_folder(folder["id"])
    except Exception:
        return None
    return next((e for e in entries if e.get("kind") == "drive#file"
                 and e.get("name") == CLOUD_FILE), None)


def cloud_mtime(api) -> float | None:
    entry = find_cloud_file(api)
    if not entry:
        return None
    try:
        info = api.file_info(entry["id"]) or {}
    except Exception:
        info = {}
    return _cloud_mtime_from_info(info) or _parse_ts(entry.get("modified_time"))


def download(api, file_id: str) -> bytes:
    import requests
    from .chrome_tokens import UA

    links = api.play_links(file_id)
    url = links.get("vip") or links.get("media") or links.get("web")
    if not url:
        raise RuntimeError("云端文件没有可用下载地址")
    resp = requests.get(url, headers={"User-Agent": UA,
                                      "Referer": "https://pan.xunlei.com/"},
                        timeout=30)
    resp.raise_for_status()
    return resp.content


def upload(api, force: bool = False) -> str:
    """Push the local bundle to the cloud, replacing any existing copy."""
    local_ts = local_mtime()
    entry = find_cloud_file(api)
    cloud_ts = None
    if entry:
        try:
            info = api.file_info(entry["id"]) or {}
        except Exception:
            info = {}
        cloud_ts = _cloud_mtime_from_info(info) or _parse_ts(entry.get("modified_time"))

    util.log(f"本地版本: {_fmt_ts(local_ts)}")
    util.log(f"云端版本: {_fmt_ts(cloud_ts)}")
    if not force:
        ans = input("  确认用本地覆盖云端存档？(yes/no): ").strip().lower()
        if ans != "yes":
            util.log("已取消上传", "WARN")
            return "cancel"

    data = json.dumps(make_bundle(), ensure_ascii=False, indent=2).encode("utf-8")
    folder = find_cloud_dir(api)
    if folder is None:
        cid = api.create_folder(CLOUD_DIR)
        util.log(f"云端没有 /{CLOUD_DIR}，已新建")
    else:
        cid = folder["id"]
    if entry:
        try:
            api.trash(entry["id"])
            time.sleep(0.5)
        except Exception as exc:
            util.log(f"覆盖前删除旧存档失败（继续上传）：{exc}", "WARN")
    api.upload_file(CLOUD_FILE, cid, data)
    local_path().write_bytes(data)
    for _ in range(6):        # wait until the drive listing reflects the upload
        if find_cloud_file(api):
            break
        time.sleep(1)
    util.log(f"已上传: /{CLOUD_DIR}/{CLOUD_FILE}（{len(data)} 字节）")
    return "uploaded"


def sync(api, choice: str | None = None, interactive: bool = True) -> str:
    """Pull the cloud bundle and let the user overwrite the local one with it."""
    entry = find_cloud_file(api)
    if not entry:
        util.log(f"云端版本: 暂无（/{CLOUD_DIR}/{CLOUD_FILE} 不存在）")
        return "none"
    try:
        info = api.file_info(entry["id"]) or {}
    except Exception:
        info = {}
    cloud_ts = _cloud_mtime_from_info(info) or _parse_ts(entry.get("modified_time"))

    util.log(f"云端版本: {_fmt_ts(cloud_ts)}")
    util.log(f"本地版本: {_fmt_ts(local_mtime())}")

    if choice not in ("cloud", "local"):
        if not interactive or not sys.stdin.isatty():
            choice = "local"
        else:
            ans = input("  用云端覆盖本地存档？(yes/no): ").strip().lower()
            choice = "cloud" if ans == "yes" else "local"

    if choice == "cloud":
        try:
            raw = download(api, entry["id"])
            bundle = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            util.log(f"云端配置无法解析（{exc}），已取消", "WARN")
            return "none"
        local_path().parent.mkdir(parents=True, exist_ok=True)
        local_path().write_bytes(raw)
        written = apply_bundle(bundle)
        util.log(f"已用云端覆盖本地（{', '.join(written) or '无字段'}）")
        return "cloud"
    util.log("保留本地配置")
    if not local_path().exists():
        save_local_bundle()
    return "local"
