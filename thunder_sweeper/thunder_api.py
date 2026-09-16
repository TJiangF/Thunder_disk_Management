"""Thin client for the 迅雷 cloud-disk (pan.xunlei.com) REST API."""

from __future__ import annotations

import json
import time
from urllib.parse import urlsplit

import requests

from . import util
from .chrome_tokens import TokenProvider

API_BASE = "https://api-pan.xunlei.com"

VIDEO_EXTENSIONS = {
    "mp4", "mkv", "avi", "mov", "wmv", "flv", "ts", "m2ts", "webm", "rmvb",
    "rm", "m4v", "mpg", "mpeg", "3gp", "vob", "f4v", "asf", "mts", "divx",
}

FILE_LIST_FILTERS = '{"phase":{"eq":"PHASE_TYPE_COMPLETE"},"trashed":{"eq":false}}'


def is_video(entry: dict) -> bool:
    mime = (entry.get("mime_type") or "").lower()
    if mime.startswith("video/"):
        return True
    name = (entry.get("name") or "").lower()
    if "." in name and name.rsplit(".", 1)[-1] in VIDEO_EXTENSIONS:
        return True
    if isinstance(entry.get("video"), dict) and entry["video"]:
        return True
    return False


class ThunderAPI:
    def __init__(self, provider: TokenProvider, cfg: dict | None = None):
        self.provider = provider
        self.cfg = cfg or util.load_config()
        self.session = requests.Session()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _action(method: str, url: str) -> str:
        """迅雷 captcha 的 action 格式：``方法:路径``（小写方法、不含查询串）。"""
        path = urlsplit(url).path or "/"
        return f"{method.lower()}:{path}"

    def _request(self, method: str, url: str, *, params=None, data=None, max_retries=None):
        retries = max_retries or self.cfg["api_retries"]
        action = self._action(method, url)
        last_exc = None
        for attempt in range(1, retries + 1):
            headers = self.provider.headers(action)
            try:
                resp = self.session.request(
                    method, url, headers=headers, params=params, data=data,
                    timeout=self.cfg["request_timeout"],
                )
            except requests.RequestException as exc:
                last_exc = exc
                self._backoff(attempt, f"网络错误: {exc}")
                continue

            if resp.status_code == 401 and attempt < retries:
                util.log("收到 401，刷新 token 后重试", "WARN")
                self.provider.force_refresh()
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                self._backoff(attempt, f"HTTP {resp.status_code}")
                continue
            if resp.status_code == 400 and attempt < retries and "captcha_invalid" in resp.text:
                util.log(f"captcha 失效（{action}），重新申请后重试", "WARN")
                self.provider.invalidate_captcha(action)
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            return resp

        raise RuntimeError(f"请求失败（重试 {retries} 次）: {url} ({last_exc})")

    def _backoff(self, attempt: int, reason: str) -> None:
        wait = min(30.0, self.cfg["api_delay"] * (2 ** attempt))
        util.log(f"{reason}，{wait:.1f}s 后重试", "WARN")
        time.sleep(wait)

    # ------------------------------------------------------------------ #
    def list_folder(self, parent_id: str) -> list[dict]:
        """List every entry (with pagination) under a folder."""
        entries: list[dict] = []
        page_token = None
        while True:
            params = {
                "parent_id": parent_id,
                "limit": 1000,
                "with_audit": "true",
                "filters": FILE_LIST_FILTERS,
            }
            if page_token:
                params["page_token"] = page_token
            resp = self._request("GET", f"{API_BASE}/drive/v1/files", params=params)
            payload = resp.json()
            entries.extend(payload.get("files") or [])
            page_token = payload.get("next_page_token")
            if not page_token:
                break
            time.sleep(self.cfg["api_delay"])
        return entries

    def file_info(self, file_id: str) -> dict:
        resp = self._request("GET", f"{API_BASE}/drive/v1/files/{file_id}", params={"space": ""})
        return resp.json()

    def trash(self, file_id: str) -> dict:
        resp = self._request("PATCH", f"{API_BASE}/drive/v1/files/{file_id}/trash")
        text = resp.text.strip()
        return resp.json() if text else {}

    def create_folder(self, name: str, parent_id: str = "") -> str:
        resp = self._request("POST", f"{API_BASE}/drive/v1/files",
                             data=json.dumps({"kind": "drive#folder", "name": name,
                                              "parent_id": parent_id, "space": ""}))
        data = resp.json()
        return (data.get("file") or {}).get("id")

    def batch_move(self, ids: list, parent_id: str) -> dict:
        resp = self._request("POST", f"{API_BASE}/drive/v1/files:batchMove",
                             data=json.dumps({"ids": list(ids),
                                              "to": {"parent_id": parent_id}, "space": ""}))
        try:
            return resp.json()
        except ValueError:
            return {}

    def get_task(self, task_id: str) -> dict:
        resp = self._request("GET", f"{API_BASE}/drive/v1/tasks", params={"id": task_id})
        try:
            tasks = resp.json().get("tasks") or []
        except ValueError:
            return {}
        return tasks[0] if tasks else {}

    def rename(self, file_id: str, name: str) -> dict:
        resp = self._request("PATCH", f"{API_BASE}/drive/v1/files/{file_id}",
                             data=json.dumps({"name": name, "space": ""}))
        try:
            return resp.json()
        except ValueError:
            return {}

    # ------------------------------------------------------------------ #
    def play_links(self, file_id: str) -> dict:
        """Return all candidate stream urls plus the raw file info.

        - ``media``: the playback/transcode link (``medias[0].link``)
        - ``vip``: VIP accelerated download link (``application/octet-stream``)
        - ``web``: normal download/play link (``web_content_link``)
        """
        info = self.file_info(file_id)
        links = info.get("links") or {}
        octet = links.get("application/octet-stream")
        vip = octet.get("url") if isinstance(octet, dict) else None
        medias = info.get("medias") or []
        media = None
        if medias:
            media_link = medias[0].get("link") if isinstance(medias[0], dict) else None
            if isinstance(media_link, dict):
                media = media_link.get("url")
        return {"media": media, "vip": vip, "web": info.get("web_content_link"), "info": info}

    def play_url(self, file_id: str, preference: str = "media") -> tuple[str, dict]:
        links = self.play_links(file_id)
        url = links.get(preference) or links.get("media") or links.get("vip") or links.get("web")
        if not url:
            raise RuntimeError(f"文件 {file_id} 没有可用的播放/下载地址")
        return url, links["info"]

    # ------------------------------------------------------------------ #
    def walk(self, state: dict | None = None, on_progress=None):
        """Recursively list the whole drive.

        ``state`` persists the frontier so a long scan can be resumed.  It
        contains ``visited`` (folder ids), ``frontier`` (folders still to scan)
        and ``videos`` (results so far).
        """
        state = state if state is not None else {}
        state.setdefault("visited", [])
        state.setdefault("videos", [])
        state.setdefault("files", [])
        state.setdefault("dirs", 0)
        visited = set(state.get("visited") or [])
        videos = state["videos"]
        all_files = state["files"]
        seen_ids = {v["id"] for v in videos}
        seen_file_ids = {f["id"] for f in all_files}
        state["dirs"] = state.get("dirs", 0)
        state.setdefault("failed", [])

        frontier = state.get("frontier")
        stack = [tuple(x) for x in frontier] if frontier else [("", "/")]

        while stack:
            parent_id, parent_path = stack.pop()
            state["frontier"] = [list(x) for x in stack]
            if parent_id and parent_id in visited:
                continue
            try:
                entries = self.list_folder(parent_id)
            except Exception as exc:
                util.log(f"列目录失败 {parent_path} ({parent_id}): {exc}", "ERROR")
                state["failed"].append([parent_id, parent_path])
                if parent_id == "":
                    # the root itself failed (usually a network/token hiccup):
                    # surface it so a scan never silently reports "0 个视频".
                    raise RuntimeError(
                        f"无法读取网盘根目录，扫描已中止：{exc}\n"
                        "请检查网络/代理后重试；若反复失败，重新运行 login。"
                    ) from exc
                continue

            visited.add(parent_id)
            state["dirs"] += 1

            for entry in entries:
                kind = entry.get("kind")
                name = entry.get("name") or entry.get("id")
                fid = entry.get("id")
                if fid and fid not in seen_file_ids:
                    seen_file_ids.add(fid)
                    try:
                        size = int(entry.get("size") or 0)
                    except (TypeError, ValueError):
                        size = 0
                    all_files.append({
                        "id": fid, "name": name, "path": parent_path, "size": size,
                        "mime_type": entry.get("mime_type"), "kind": kind,
                        "is_video": is_video(entry),
                    })
                if kind == "drive#folder":
                    stack.append((fid, f"{parent_path.rstrip('/')}/{name}"))
                elif is_video(entry):
                    if fid in seen_ids:
                        continue
                    seen_ids.add(fid)
                    videos.append(_normalize_video(entry, parent_path))

            state["videos"] = videos
            state["files"] = all_files
            state["visited"] = list(visited)
            if on_progress:
                on_progress(len(videos), state["dirs"], parent_path)
            time.sleep(self.cfg["api_delay"])

        return state


def extract_meta(info: dict) -> dict:
    """Pull duration/resolution out of a file_info payload (via ``medias``)."""
    media = {}
    medias = info.get("medias") or []
    if medias and isinstance(medias[0], dict):
        media = medias[0].get("video") or {}
    duration = media.get("duration") or info.get("duration")
    try:
        duration = float(duration) if duration else None
    except (TypeError, ValueError):
        duration = None
    return {
        "duration": duration,
        "width": media.get("width"),
        "height": media.get("height"),
        "frame_rate": media.get("frame_rate"),
    }


def _normalize_video(entry: dict, parent_path: str) -> dict:
    video_meta = entry.get("video") if isinstance(entry.get("video"), dict) else {}
    try:
        size = int(entry.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    duration = video_meta.get("duration") or entry.get("duration")
    try:
        duration = float(duration) if duration else None
    except (TypeError, ValueError):
        duration = None
    return {
        "id": entry.get("id"),
        "name": entry.get("name"),
        "path": parent_path,
        "size": size,
        "mime_type": entry.get("mime_type"),
        "duration": duration,
        "width": video_meta.get("width"),
        "height": video_meta.get("height"),
        "frame_rate": video_meta.get("frame_rate"),
        "thumbs": [],
        "done": False,
        "error": None,
    }
