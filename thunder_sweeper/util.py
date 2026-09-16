"""Common helpers: paths, logging, JSON IO, formatting."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

APP_NAME = "ThunderSweeper"
APP_VERSION = "1.0.0"

SOURCE_ROOT = Path(__file__).resolve().parent.parent


def _default_home() -> Path:
    """Where user data (tokens, scan results, thumbs, config) lives.

    - ``THUNDER_SWEEPER_HOME`` env var wins (handy for tests / portables).
    - Running from source keeps everything inside the project folder
      (backwards compatible with existing installs).
    - A packaged app (PyInstaller) must not write inside its own bundle,
      so it uses the platform's user-data directory.
    """
    env = os.environ.get("THUNDER_SWEEPER_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if not getattr(sys, "frozen", False):
        return SOURCE_ROOT
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


ROOT = _default_home()
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
    "chrome_path": "",  # 留空则自动探测（Chrome/Edge/Brave/Chromium）
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
        uniq = os.urandom(3).hex()
        dst = BACKUP_DIR / f"{path.name}.{ts}.{uniq}.bak"
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


# --------------------------------------------------------------------------- #
# Chrome discovery
# --------------------------------------------------------------------------- #
_BROWSER_APPS_MAC = [
    "Google Chrome",
    "Google Chrome Canary",
    "Microsoft Edge",
    "Brave Browser",
    "Chromium",
    "Vivaldi",
]
_BROWSER_BINS = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge", "microsoft-edge-stable", "brave-browser", "vivaldi",
]
_BROWSER_PATHS_WIN = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_chrome(cfg: dict | None = None) -> str | None:
    """Locate a Chromium-based browser to drive for login/token harvest.

    Order: explicit ``chrome_path`` in config → ``THUNDER_SWEEPER_CHROME`` env →
    ``PATH`` → well-known macOS/Windows install locations.
    """
    configured = (cfg or {}).get("chrome_path")
    if configured and Path(configured).exists():
        return configured
    env = os.environ.get("THUNDER_SWEEPER_CHROME")
    if env and Path(env).exists():
        return env
    for name in _BROWSER_BINS:
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "darwin":
        for app in _BROWSER_APPS_MAC:
            exe = Path(f"/Applications/{app}.app/Contents/MacOS/{app}")
            if exe.exists():
                return str(exe)
            exe = Path.home() / "Applications" / f"{app}.app" / "Contents" / "MacOS" / app
            if exe.exists():
                return str(exe)
    elif sys.platform.startswith("win"):
        for path in _BROWSER_PATHS_WIN:
            if Path(path).exists():
                return path
    return None


def data_dir_display() -> str:
    return str(DATA_DIR)


def app_version() -> str:
    return APP_VERSION


# --------------------------------------------------------------------------- #
# interactive terminal helpers (progress bar, prompts, single-key listener)
# --------------------------------------------------------------------------- #
def prompt_int(question: str, default: int, minimum: int | None = None,
               maximum: int | None = None, allow_blank: bool = True) -> int:
    """Ask the user for an integer, clamped to [minimum, maximum]."""
    hint = f"（默认 {default}"
    if maximum is not None:
        hint += f"，最大 {maximum}"
    hint += "）"
    while True:
        raw = input(f"{question}{hint}: ").strip()
        if not raw and allow_blank:
            value = default
        else:
            try:
                value = int(raw)
            except ValueError:
                print("  请输入数字。")
                continue
        if minimum is not None and value < minimum:
            print(f"  不能小于 {minimum}。")
            continue
        if maximum is not None and value > maximum:
            print(f"  不能大于 {maximum}。")
            continue
        return value


class ProgressBar:
    """Simple single-line progress bar (safe when stderr is not a tty)."""

    def __init__(self, total: int, label: str = "", width: int = 28, stream=None):
        self.total = max(0, int(total))
        self.label = label
        self.width = width
        self.stream = stream or sys.stderr
        self.done = 0
        self.ok = 0
        self.fail = 0
        self._last_len = 0
        self._tty = bool(getattr(self.stream, "isatty", lambda: False)())

    def start(self) -> None:
        self.update(0)

    def update(self, done: int | None = None, ok: int | None = None,
               fail: int | None = None, label: str | None = None) -> None:
        if done is not None:
            self.done = int(done)
        if ok is not None:
            self.ok = int(ok)
        if fail is not None:
            self.fail = int(fail)
        if label is not None:
            self.label = label
        total = self.total or 1
        frac = min(1.0, self.done / total)
        filled = int(round(frac * self.width))
        bar = "█" * filled + "░" * (self.width - filled)
        head = f"{self.done}/{self.total}"
        extra = f" ✓{self.ok}" + (f" ✗{self.fail}" if self.fail else "")
        text = f"  [{bar}] {head}{extra}  {self.label}"
        if self._tty:
            pad = max(0, self._last_len - len(text))
            self.stream.write("\r" + text + " " * pad)
            self.stream.flush()
            self._last_len = len(text)
        else:
            # non-tty: print a line only on completion
            if self.done >= self.total:
                self.stream.write(text.strip() + "\n")
                self.stream.flush()

    def finish(self) -> None:
        self.update(self.total)
        if self._tty:
            self.stream.write("\n")
            self.stream.flush()
