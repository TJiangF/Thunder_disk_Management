"""Common helpers: paths, logging, JSON IO, formatting."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
THUMB_DIR = DATA_DIR / "thumbs"
TOKENS_FILE = DATA_DIR / "tokens.json"
VIDEOS_FILE = DATA_DIR / "videos.json"
FILES_FILE = DATA_DIR / "files.json"
CLASSIFIED_FILE = DATA_DIR / "classified.json"
CLASSIFY_RULES_FILE = DATA_DIR / "classify_rules.json"
MANUAL_CATS_FILE = DATA_DIR / "manual_categories.json"
CATEGORIES_FILE = DATA_DIR / "categories.json"
QUEUE_FILE = DATA_DIR / "queue.json"
SHOTS_STATE_FILE = DATA_DIR / "shots_state.json"
SELECTIONS_FILE = DATA_DIR / "selections.json"
SCAN_STATE_FILE = DATA_DIR / "scan_state.json"
REVIEW_PROGRESS_FILE = DATA_DIR / "review_progress.json"
CHROME_PROFILE = ROOT / ".chrome-profile"
CONFIG_FILE = ROOT / "config.json"

DEFAULT_CONFIG = {
    "chrome_path": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "debug_port": 9222,
    "fractions": [0.06, 0.18, 0.30, 0.42, 0.54, 0.66, 0.78, 0.90],
    "thumb_width": 480,
    "thumb_quality": 4,
    "request_timeout": 30,
    "ffmpeg_timeout": 60,
    "video_timeout": 300,
    "api_delay": 0.25,
    "api_retries": 5,
    "organize_base": "/整理",
    "organize_small_mb": 20,
    "organize_large_mb": 100,
    "organize_chunk": 8,
    "organize_delay": 1.0,
    "workers": 3,
    "frame_retries": 2,
    "link_preference": "media",
}

_COLORS = {
    "INFO": "\033[32m",
    "WARN": "\033[33m",
    "ERROR": "\033[31m",
    "DEBUG": "\033[90m",
}
_RESET = "\033[0m"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)


def log(message: str, level: str = "INFO") -> None:
    color = _COLORS.get(level, "")
    ts = datetime.now().strftime("%H:%M:%S")
    use_color = sys.stderr.isatty()
    prefix = f"{color}[{ts}] [{level}]{_RESET}" if use_color else f"[{ts}] [{level}]"
    print(f"{prefix} {message}", flush=True)


def human_size(num) -> str:
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def human_duration(seconds) -> str:
    try:
        seconds = int(float(seconds))
    except (TypeError, ValueError):
        return "-"
    if seconds <= 0:
        return "-"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log(f"读取 {path} 失败: {exc}", "WARN")
        return default


BACKUP_DIR = DATA_DIR / "backups"
_BACKUP_NAMES = {
    "categories.json", "classify_rules.json", "manual_categories.json",
    "selections.json", "review_progress.json", "config.json",
}


def backup_file(path: Path, keep: int = 20) -> None:
    """Keep timestamped copies of small user-authored config/data files."""
    if not path.exists():
        return
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = BACKUP_DIR / f"{path.name}.{ts}.bak"
        shutil.copy2(path, dst)
        for old in sorted(BACKUP_DIR.glob(path.name + ".*.bak"))[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass


def atomic_write_json(path: Path, obj) -> None:
    ensure_dirs()
    if path.name in _BACKUP_NAMES:
        backup_file(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    user = read_json(CONFIG_FILE, {}) or {}
    cfg.update(user)
    return cfg


def cpu_count() -> int:
    """Logical CPU threads of this machine."""
    return os.cpu_count() or 4


def cap_workers(n) -> int:
    """Clamp a requested worker count to [1, cpu_count()]."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, cpu_count()))


def sleep(seconds: float) -> None:
    time.sleep(max(0.0, seconds))
