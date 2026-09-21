"""Generate thumbnails from the VIP accelerated stream via ffmpeg.

``ffmpeg -ss`` placed *before* ``-i`` performs an HTTP range seek, so only the
bytes around the requested keyframe are downloaded.  That keeps the whole
sweep light on bandwidth even for very large videos.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import util
from .chrome_tokens import UA


class ToolMissing(RuntimeError):
    pass


def _ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return None


def _ffprobe_exe():
    return shutil.which("ffprobe")


def ensure_tools() -> None:
    if not _ffmpeg_exe():
        raise ToolMissing(
            "缺少 ffmpeg，请安装：brew install ffmpeg / winget install ffmpeg"
            "（或 pip install imageio-ffmpeg）"
        )


def _input_headers(url: str = "") -> list[str]:
    if url.startswith("http://") or url.startswith("https://"):
        return ["-user_agent", UA, "-headers", "Referer: https://pan.xunlei.com/\r\n"]
    return []


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")


def probe(url: str, cfg: dict) -> dict:
    ensure_tools()
    ffprobe = _ffprobe_exe()
    if ffprobe:
        return _probe_json(ffprobe, url, cfg)
    return _probe_text(url, cfg)


def _probe_text(url: str, cfg: dict) -> dict:
    cmd = [
        _ffmpeg_exe(), "-hide_banner", "-nostdin",
        "-rw_timeout", str(int(cfg["ffmpeg_timeout"] * 1_000_000)),
        *_input_headers(url), "-i", url,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=cfg["ffmpeg_timeout"] + 15)
    except subprocess.TimeoutExpired:
        return {}
    text = (out.stderr or "") + (out.stdout or "")
    result: dict = {}
    m = _DURATION_RE.search(text)
    if m:
        result["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    mv = _VIDEO_RE.search(text)
    if mv:
        result["width"], result["height"] = int(mv.group(1)), int(mv.group(2))
    return result


def _probe_json(ffprobe: str, url: str, cfg: dict) -> dict:
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams",
        "-rw_timeout", str(int(cfg["ffmpeg_timeout"] * 1_000_000)),
        *_input_headers(url), url,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=cfg["ffmpeg_timeout"] + 15)
    except subprocess.TimeoutExpired:
        util.log("ffprobe 超时", "WARN")
        return {}
    if out.returncode != 0:
        util.log(f"ffprobe 失败: {out.stderr.strip()[:200]}", "WARN")
        return {}

    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return {}

    result = {}
    fmt = data.get("format") or {}
    try:
        result["duration"] = float(fmt.get("duration")) if fmt.get("duration") else None
    except (TypeError, ValueError):
        result["duration"] = None
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video":
            result["width"] = stream.get("width")
            result["height"] = stream.get("height")
            break
    return result


def grab(url: str, seconds: float, out_path: Path, cfg: dict) -> str:
    """Return 'ok' | 'timeout' | 'error'."""
    ensure_tools()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    width = cfg["thumb_width"]
    cmd = [
        _ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-rw_timeout", str(int(cfg["ffmpeg_timeout"] * 1_000_000)),
        *_input_headers(url),
        "-ss", f"{max(0.0, seconds):.3f}",
        "-i", url,
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", str(cfg["thumb_quality"]),
        "-y", str(out_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=cfg["ffmpeg_timeout"])
    except subprocess.TimeoutExpired:
        util.log(f"截图超时 @ {seconds:.0f}s", "WARN")
        return "timeout"
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        if proc.stderr.strip():
            util.log(f"截图失败 @ {seconds:.0f}s: {proc.stderr.strip()[:200]}", "WARN")
        return "error"
    return "ok"


def is_complete(video: dict, needed: int) -> bool:
    return len(disk_thumbs(video.get("id"), needed)) >= needed


def disk_thumbs(video_id, needed: int) -> list[str]:
    """Existing thumbnail paths (relative to data/) for a video."""
    folder = util.THUMB_DIR / str(video_id)
    if not folder.exists():
        return []
    found = []
    for i in range(1, needed + 1):
        p = folder / f"{i}.jpg"
        if p.exists() and p.stat().st_size > 0:
            found.append(str(p.relative_to(util.DATA_DIR)))
    return found


def process_many(provider, videos: list[dict], cfg: dict, workers: int | None = None,
                 progress=None, on_done=None, live=None, stop=None,
                 api_factory=None) -> list[dict]:
    """Screenshot many videos concurrently.

    Each worker thread gets its own API client.  In cloud mode that's a
    ``ThunderAPI`` built from ``provider``; pass ``api_factory`` (callable ->
    client) for local mode (e.g. :class:`local_disk.LocalAPI`).  When ``live``
    (a :class:`util.LiveDisplay`) is given, the videos in progress are painted
    into its dedicated lines; otherwise a 15s heartbeat logs them so a slow
    frame does not look like a hang.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .thunder_api import ThunderAPI

    workers = util.cap_workers(workers or cfg.get("workers", 3))
    local = threading.local()
    counter = {"n": 0}
    lock = threading.Lock()
    in_flight: dict = {}
    order: list = []
    inflight_lock = threading.Lock()
    hb_stop = threading.Event()
    if live is not None:
        live.console(f"开始截图：目标 {len(videos)} 个，并发 {workers}")

    def get_api():
        api = getattr(local, "api", None)
        if api is None:
            if api_factory is not None:
                api = api_factory()
            else:
                api = ThunderAPI(provider, cfg)
            local.api = api
        return api

    def snapshot() -> list[str]:
        with inflight_lock:
            return [in_flight[k]["text"] for k in order if k in in_flight]

    def publish() -> None:
        if live is not None:
            live.set_tasks(snapshot())

    def task(video):
        if stop is not None and stop.is_set():
            return video
        key = id(video)
        name = video.get("name") or str(video.get("id"))
        with inflight_lock:
            in_flight[key] = {"name": name, "text": name}
            order.append(key)
        publish()

        def report(text):
            with inflight_lock:
                slot = in_flight.get(key)
                if slot is not None:
                    slot["text"] = text
            publish()

        try:
            process_video(get_api(), video, cfg, resume=True,
                          verbose=(workers == 1 and live is None),
                          status=(report if live is not None else None),
                          debug=(live.console if live is not None else None),
                          stop=stop)
        except Exception as exc:
            video["error"] = str(exc)
            if live is not None:
                live.console(f"[{name}] 处理异常: {exc}")
            else:
                util.log(f"[{name}] 处理异常: {exc}", "ERROR")
        finally:
            with inflight_lock:
                in_flight.pop(key, None)
                if key in order:
                    order.remove(key)
            publish()
        if on_done:
            try:
                on_done(video)
            except Exception:
                pass
        with lock:
            counter["n"] += 1
            done = counter["n"]
        if progress:
            try:
                progress(done, len(videos), video)
            except Exception:
                pass
        return video

    def heartbeat():
        while not hb_stop.wait(15):
            with inflight_lock:
                names = [v["name"] for v in in_flight.values()]
            if names:
                head = ", ".join(names[:6])
                more = f" …等 {len(names)} 个" if len(names) > 6 else ""
                util.log(f"进行中：{head}{more}")

    hb = None
    if live is None:
        hb = threading.Thread(target=heartbeat, daemon=True)
        hb.start()
    try:
        if workers == 1:
            for v in videos:
                task(v)
        else:
            ex = ThreadPoolExecutor(max_workers=workers)
            futures = [ex.submit(task, v) for v in videos]
            try:
                for _ in as_completed(futures):
                    pass
            except KeyboardInterrupt:
                util.log("已中断，取消未开始的任务（进行中的会在超时后自行结束）", "WARN")
                for f in futures:
                    f.cancel()
                ex.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                ex.shutdown(wait=False)
    finally:
        hb_stop.set()
    return videos


def _fresh_url(api, file_id: str, cfg: dict) -> str:
    """Fetch a fresh stream url.  Thunder's links are short-lived, so a new one
    must be requested for every frame."""
    links = api.play_links(file_id)
    preference = cfg.get("link_preference", "media")
    url = links.get(preference) or links.get("media") or links.get("vip") or links.get("web")
    if not url:
        raise RuntimeError("文件没有可用的播放/下载地址")
    return url


def process_video(api, video: dict, cfg: dict, resume: bool = True,
                  fractions: list[float] | None = None, verbose: bool = True,
                  status=None, debug=None, stop=None) -> dict:
    from . import thunder_api

    def say(message, level="INFO"):
        if level in ("WARN", "ERROR") and debug is not None:
            try:
                debug(message)
            except Exception:
                pass
            return
        if verbose:
            util.log(message, level)

    if stop is not None and stop.is_set():
        video["error"] = "已停止"
        video["done"] = False
        return video

    short = video.get("name") or str(video.get("id"))

    def report(stage):
        if status is None:
            return
        try:
            status(f"{stage}  {short}")
        except Exception:
            pass

    fractions = fractions or cfg["fractions"]
    needed = len(fractions)
    out_dir = util.THUMB_DIR / str(video["id"])
    out_dir.mkdir(parents=True, exist_ok=True)

    def rel(path: Path) -> str:
        return str(path.relative_to(util.DATA_DIR))

    existing = []
    for i in range(1, needed + 1):
        p = out_dir / f"{i}.jpg"
        existing.append(p if p.exists() and p.stat().st_size > 0 else None)

    if resume and all(existing) and video.get("duration"):
        video["thumbs"] = [rel(p) for p in existing]
        video["done"] = True
        return video

    name = video.get("name")

    say(f"[{name}] 获取播放直链...")
    report("获取直链")
    t0 = time.time()
    try:
        links = api.play_links(video["id"])
    except Exception as exc:
        video["error"] = f"获取直链失败: {exc}"
        say(f"[{name}] {video['error']}", "ERROR")
        report("取链接失败")
        return video

    meta = thunder_api.extract_meta(links["info"])
    video["duration"] = video.get("duration") or meta.get("duration")
    video["width"] = video.get("width") or meta.get("width")
    video["height"] = video.get("height") or meta.get("height")
    say(f"[{name}] 直链已就绪（{time.time() - t0:.1f}s）")
    report("直链就绪")

    if not video.get("duration"):
        say(f"[{name}] 元数据无时长，尝试探测...")
        report("探测时长")
        preference = cfg.get("link_preference", "media")
        url0 = (links.get(preference) or links.get("media")
                or links.get("vip") or links.get("web"))
        if url0:
            probed = probe(url0, cfg)
            video["duration"] = probed.get("duration")
            video["width"] = video.get("width") or probed.get("width")
            video["height"] = video.get("height") or probed.get("height")

    duration = video.get("duration")
    if duration:
        times = [duration * f for f in fractions]
        say(f"[{name}] 时长 {util.human_duration(duration)}，开始截图（每张单独取链）")
    else:
        times = [5, 15, 30, 60, 120, 300][:needed]
        say(f"[{name}] 无法获取时长，改用固定时间点", "WARN")

    retries = max(1, int(cfg.get("frame_retries", 2)))
    video_timeout = float(cfg.get("video_timeout", 300))
    started = time.time()
    thumbs: list[str] = []
    timeouts = 0
    for i, seconds in enumerate(times, start=1):
        if stop is not None and stop.is_set():
            say(f"[{name}] 已停止，跳过剩余 {needed - i + 1} 张", "WARN")
            break
        out_path = out_dir / f"{i}.jpg"
        if resume and out_path.exists() and out_path.stat().st_size > 0:
            thumbs.append(rel(out_path))
            report(f"已有 {i}/{needed}")
            say(f"[{name}] 第 {i}/{needed} 张已存在，跳过")
            continue

        if time.time() - started > video_timeout:
            report("超时放弃剩余")
            say(f"[{name}] 超过单视频时限 {video_timeout:.0f}s，跳过剩余 {needed - i + 1} 张", "WARN")
            break

        report(f"截图 {i}/{needed}")
        res = "error"
        for attempt in range(1, retries + 1):
            t = time.time()
            try:
                url = _fresh_url(api, video["id"], cfg)
            except Exception as exc:
                res = "error"
                say(f"[{name}] 第 {i}/{needed} 张取链失败（{attempt}/{retries}）: {exc}", "WARN")
                time.sleep(1)
                continue
            res = grab(url, seconds, out_path, cfg)
            if res == "ok":
                thumbs.append(rel(out_path))
                say(f"[{name}] 第 {i}/{needed} 张完成（{time.time() - t:.1f}s）")
                break
            kind = "超时" if res == "timeout" else "失败"
            report(f"重试 {i}/{needed} {attempt}/{retries}")
            say(f"[{name}] 第 {i}/{needed} 张第 {attempt}/{retries} 次{kind}（{time.time() - t:.1f}s）", "WARN")
            if res == "timeout":
                break  # 超时说明该区段不可达，重试无意义
            time.sleep(1)

        if res == "ok":
            timeouts = 0
            report(f"截图 {i}/{needed} ✓")
        elif res == "timeout":
            timeouts += 1
            report(f"截图 {i}/{needed} ✗")
            if timeouts >= 2:
                say(f"[{name}] 连续 {timeouts} 张超时，放弃该视频剩余帧", "WARN")
                break
        else:
            report(f"截图 {i}/{needed} ✗")
            say(f"[{name}] 第 {i}/{needed} 张最终失败", "ERROR")

    video["thumbs"] = thumbs
    video["done"] = len(thumbs) > 0
    if len(thumbs) < needed:
        video["error"] = f"仅生成 {len(thumbs)}/{needed} 张截图"
        say(f"[{name}] {video['error']}", "WARN")
    else:
        video["error"] = None
    return video
