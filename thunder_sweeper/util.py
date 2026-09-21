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


def ensure_utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr.

    On Windows the console may default to cp1252/cp936; Chinese ("ThunderSweeper")
    and block chars (``█░``) then crash with UnicodeEncodeError.  Reconfiguring
    to UTF-8 is a harmless no-op on macOS/Linux.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _os_home() -> Path:
    """Platform per-user data directory for this app."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


DEFAULT_HOME = _os_home()
LOCATION_FILE = DEFAULT_HOME / "location.txt"


def _stored_home() -> Path | None:
    """The custom data root saved via :func:`set_home`, if any."""
    try:
        text = LOCATION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(text).expanduser().resolve() if text else None


def _default_home() -> Path:
    """Where user data (tokens, scan results, thumbs, config) lives.

    Precedence:

    1. ``THUNDER_SWEEPER_HOME`` env var (portables / scripts / tests);
    2. a root saved with :func:`set_home` (kept in ``location.txt``);
    3. the platform user-data dir — on macOS
       ``~/Library/Application Support/ThunderSweeper``.

    Source runs and the packaged app share the same default.
    """
    env = os.environ.get("THUNDER_SWEEPER_HOME")
    if env:
        return Path(env).expanduser().resolve()
    stored = _stored_home()
    if stored is not None:
        return stored
    return DEFAULT_HOME


def _apply_root(root: Path) -> None:
    """(Re)point every data path at ``root`` for the current process."""
    g = globals()
    data = root / "data"
    g["ROOT"] = root
    g["DATA_DIR"] = data
    g["THUMB_DIR"] = data / "thumbs"
    g["TOKENS_FILE"] = data / "tokens.json"
    g["VIDEOS_FILE"] = data / "videos.json"
    g["FILES_FILE"] = data / "files.json"
    g["CLASSIFIED_FILE"] = data / "classified.json"
    g["CLASSIFY_RULES_FILE"] = data / "classify_rules.json"
    g["MANUAL_CATS_FILE"] = data / "manual_categories.json"
    g["CATEGORIES_FILE"] = data / "categories.json"
    g["QUEUE_FILE"] = data / "queue.json"
    g["SHOTS_STATE_FILE"] = data / "shots_state.json"
    g["SELECTIONS_FILE"] = data / "selections.json"
    g["SCAN_STATE_FILE"] = data / "scan_state.json"
    g["REVIEW_PROGRESS_FILE"] = data / "review_progress.json"
    g["RATINGS_FILE"] = data / "ratings.json"
    g["CHROME_PROFILE"] = root / ".chrome-profile"
    g["CONFIG_FILE"] = root / "config.json"
    g["BACKUP_DIR"] = data / "backups"


_apply_root(_default_home())


def set_home(path=None) -> Path:
    """Change the data root, persist it and apply it to the current process.

    ``path`` empty/``None`` resets to the default location.  The choice is saved
    to ``<default>/location.txt`` so it survives restarts, for both the source
    runner and the packaged app.
    """
    DEFAULT_HOME.mkdir(parents=True, exist_ok=True)
    if path:
        root = Path(path).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        LOCATION_FILE.write_text(str(root), encoding="utf-8")
    else:
        root = DEFAULT_HOME
        try:
            LOCATION_FILE.unlink()
        except OSError:
            pass
    _apply_root(root)
    ensure_dirs()
    return root

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


_live_sink = None


def set_live_sink(fn) -> None:
    """While a live panel is active, route :func:`log` output into it."""
    global _live_sink
    _live_sink = fn


def log(message: str, level: str = "INFO") -> None:
    sink = _live_sink
    if sink is not None:
        try:
            sink(message, level)
            return
        except Exception:
            pass
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
    """Coerce a requested worker count to a positive integer (no upper cap)."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 1
    return max(1, n)


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


def _oneline(text) -> str:
    """Collapse any whitespace (incl. newlines) so a value can be shown on one row."""
    return " ".join(str(text).split())


class LiveDisplay:
    """Fixed-position status panel for long batch jobs.

    Layout (anchored to the bottom of the terminal, redrawn in place so it never
    scrolls or jumps)::

        [██████░░░░░░░░] 12/50 ✓10 ✗2  截图      <- progress bar (fixed row)
          线程1  文件名A.mp4      截图 3/8
          线程2  文件名B.mp4      取直链
          ── 日志 ──
          12:01:05  文件名B.mp4 仅生成 6/8 张    <- newest message on top
          12:01:02  第 4/8 张超时，放弃剩余帧
          12:00:58  开始截图：目标 50 个，并发 3

    The console keeps the most recent ``console_lines`` messages with the newest
    on top (older ones shift down); the full history is retained in memory.  All
    text is flattened to a single line, and while the panel is active every
    :func:`log` call is routed into the console, so nothing can scroll the screen
    and duplicate the bar.  When ``stream`` is not a tty the panel stays silent
    and only prints a summary on :meth:`finish`.
    """

    def __init__(self, total: int, slots: int = 1, label: str = "",
                 width: int = 28, console_lines: int = 6, stream=None,
                 interval: float = 0.4):
        self.total = max(0, int(total))
        self.slots = max(0, int(slots))
        self.label = label
        self.width = width
        self.console_lines = max(0, int(console_lines))
        self.stream = stream or sys.stderr
        self.interval = max(0.05, float(interval))
        self.done = 0
        self.ok = 0
        self.fail = 0
        self.tasks: list[str] = []
        self._history: list[str] = []
        self._lock = threading.RLock()
        self._tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self._stop = threading.Event()
        self._thread = None

    def start(self) -> None:
        if not self._tty:
            return
        set_live_sink(self._receive_log)
        self.stream.write("\033[?25l")
        self.stream.flush()
        self._thread = threading.Thread(target=self._tick, daemon=True)
        self._thread.start()
        with self._lock:
            self._render_locked()

    def set_progress(self, done=None, ok=None, fail=None, label=None) -> None:
        with self._lock:
            if done is not None:
                self.done = int(done)
            if ok is not None:
                self.ok = int(ok)
            if fail is not None:
                self.fail = int(fail)
            if label is not None:
                self.label = label
            self._render_locked()

    def set_tasks(self, tasks) -> None:
        with self._lock:
            self.tasks = [_oneline(t) for t in tasks]
            self._render_locked()

    def console(self, message: str) -> None:
        """Append a message to the console area (newest shown on top)."""
        line = f"{datetime.now().strftime('%H:%M:%S')}  {_oneline(message)}"
        with self._lock:
            if not self._tty:
                print(line, file=self.stream, flush=True)
                return
            self._history.append(line)
            self._render_locked()

    def log(self, message: str) -> None:
        self.console(message)

    def finish(self, label: str | None = None) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        set_live_sink(None)
        if label is not None:
            self.label = label
        summary = self._summary()
        with self._lock:
            if not self._tty:
                print(summary, file=self.stream, flush=True)
                return
            top = self._clear_region_locked()
            self.stream.write("\033[?25h")
            self.stream.write(f"\033[{top};1H")
            self.stream.write(summary + "\n")
            self.stream.flush()

    def _receive_log(self, message, level: str = "INFO") -> None:
        prefix = f"[{level}] " if level and level != "INFO" else ""
        self.console(prefix + str(message))

    def _tick(self) -> None:
        while not self._stop.wait(self.interval):
            with self._lock:
                self._render_locked()

    def _bar(self) -> str:
        frac = min(1.0, self.done / (self.total or 1))
        filled = int(round(frac * self.width))
        return "█" * filled + "░" * (self.width - filled)

    def _summary(self) -> str:
        extra = f" ✓{self.ok}" + (f" ✗{self.fail}" if self.fail else "")
        label = f"  {self.label}" if self.label else ""
        return f"[{self._bar()}] {self.done}/{self.total}{extra}{label}"

    def _lines(self, limit: int):
        bar = self._summary()
        workers = []
        for i in range(self.slots):
            text = self.tasks[i] if i < len(self.tasks) else ""
            workers.append(("  " + text)[:limit] if text else "")
        console = []
        if self.console_lines:
            console.append(("  ── 日志 ──")[:limit])
            recent = list(reversed(self._history[-self.console_lines:]))
            for j in range(self.console_lines):
                console.append(("  " + recent[j])[:limit] if j < len(recent) else "")
        return bar, workers, console

    def _size(self) -> tuple[int, int]:
        try:
            size = os.get_terminal_size(self.stream.fileno())
            return size.lines, size.columns
        except (AttributeError, ValueError, OSError):
            size = shutil.get_terminal_size((100, 24))
            return size.lines, size.columns

    def _region_locked(self) -> tuple[int, list[str]]:
        rows, cols = self._size()
        usable = max(1, rows - 1)
        limit = max(0, cols - 1)
        bar, workers, console = self._lines(limit)
        room = usable - 1 - len(console)
        if room < len(workers):
            workers = workers[: max(0, room)]
        lines = [bar, *workers, *console]
        if len(lines) > usable:
            lines = lines[:usable]
        top = max(1, usable - len(lines) + 1)
        return top, lines

    def _render_locked(self) -> None:
        if not self._tty:
            return
        rows, cols = self._size()
        top, lines = self._region_locked()
        limit = max(0, cols - 1)
        buf = []
        for i, line in enumerate(lines):
            buf.append(f"\033[{top + i};1H\033[2K{line[:limit]}")
        self.stream.write("".join(buf))
        self.stream.flush()

    def _clear_region_locked(self) -> int:
        rows, _ = self._size()
        top, _ = self._region_locked()
        buf = []
        for row in range(top, rows + 1):
            buf.append(f"\033[{row};1H\033[2K")
        buf.append(f"\033[{top};1H")
        self.stream.write("".join(buf))
        self.stream.flush()
        return top
