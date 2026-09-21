"""Local disk management: scan a folder, screenshot, review, organize, trash.

Mirrors the cloud mode's data schema so ``classify`` / ``dedupe`` / ``organize``
/ ``screenshots`` / ``review_server`` work unchanged.  All records are written
to the *local* data set (``data_local/``) so they never mix with -- nor get
sync'd to the cloud along with -- the cloud records.

Interned paths: every folder/file ``path`` is stored in a cloud-style form with
a leading ``/`` (e.g. ``/E:/Videos`` on Windows, ``/Volumes/Stuff`` on macOS) so
the cloud plan/tree helpers that assume ``/foo/bar`` keep working.  ``/E:/..``
or ``/Volumes/..`` maps back to a real ``Path`` via :func:`extern`.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import string
import sys
from pathlib import Path

from . import util


# --------------------------------------------------------------------------- #
# path helpers
# --------------------------------------------------------------------------- #
def abs_path(p) -> str:
    """Real absolute filesystem path (platform separators)."""
    return str(Path(p).expanduser().resolve())


def intern(p) -> str:
    """Real abs path -> cloud-style path with a leading ``/``.

    ``E:\\Videos\\sub`` -> ``/E:/Videos/sub``   (Windows)
    ``/Volumes/Stuff``  -> ``/Volumes/Stuff``   (macOS / Linux)
    """
    text = abs_path(p).replace("\\", "/")
    return "/" + text.lstrip("/")


def extern(p) -> Path:
    """Inverse of :func:`intern`: cloud-style path -> real ``Path``."""
    return Path(str(p).lstrip("/"))


def video_id(abs_path: str) -> str:
    """Stable short id for a local file (= its absolute path hash)."""
    return hashlib.md5(abs_path.encode("utf-8", "replace")).hexdigest()


def local_path_for(video: dict) -> str:
    """Real abs path for a video record (``local_path`` or derived from intern)."""
    p = video.get("local_path")
    if p:
        return p
    return str(extern(video.get("path", "")).joinpath(video.get("name") or ""))


def drives() -> list[str]:
    """Likely local disks / mount roots the user may manage."""
    if sys.platform.startswith("win"):
        out = []
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.exists(root):
                out.append(root)
        return out
    if sys.platform == "darwin":
        vols = Path("/Volumes")
        out = ["/"]
        if vols.exists():
            out += [str(v) for v in vols.iterdir() if v.is_dir()]
        return out
    out = ["/"]
    for mp in ("/media", "/mnt"):
        base = Path(mp)
        if base.exists():
            out += [str(p) for p in base.iterdir() if p.is_dir()]
    return out


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #
def is_video_name(name: str) -> bool:
    from .thunder_api import VIDEO_EXTENSIONS

    n = (name or "").lower()
    return "." in n and n.rsplit(".", 1)[-1] in VIDEO_EXTENSIONS


TRASH_DIR_NAME = ".ThunderSweeper_Trash"


def scan(root, state: dict = None, on_progress=None) -> dict:
    """Recursively walk ``root`` and collect files + videos.

    Resumable: pass the previous ``state`` (with its ``frontier``) to continue.
    Never modifies anything on disk — read-only traversal.
    """
    root = Path(root).expanduser().resolve()

    if state is None:
        state = {}
    state.setdefault("frontier", [str(root)])
    state.setdefault("videos", [])
    state.setdefault("files", [])
    state.setdefault("dirs", 0)
    state.setdefault("failed", [])
    state.setdefault("root", str(root))

    visited = {str(p) for p in state.get("visited") or []}
    frontier = [str(p) for p in state["frontier"] if str(p) not in visited]

    while frontier:
        state["frontier"] = [str(p) for p in frontier]
        cur = frontier.pop()
        if cur in visited:
            continue
        visited.add(cur)

        dir_in = intern(cur)
        entries = []
        try:
            with os.scandir(cur) as it:
                for e in it:
                    entries.append(e)
        except OSError as exc:
            util.log(f"无法读取目录 {cur}: {exc}", "WARN")
            state["failed"].append([cur, dir_in])
            continue

        state["dirs"] += 1

        for e in entries:
            name = e.name
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if name == TRASH_DIR_NAME:
                    continue
                frontier.append(e.path)
                continue

            try:
                size = e.stat(follow_symlinks=False).st_size
            except OSError:
                size = 0
            abs_p = os.path.abspath(e.path)
            rec = {
                "id": video_id(abs_p),
                "name": name,
                "path": dir_in,
                "size": size,
                "mime_type": mimetypes.guess_type(name)[0],
                "kind": "local#file",
                "is_video": is_video_name(name),
                "local_path": abs_p,
            }
            state["files"].append(rec)
            if rec["is_video"]:
                state["videos"].append({
                    "id": rec["id"],
                    "name": name,
                    "path": dir_in,
                    "size": size,
                    "mime_type": rec["mime_type"],
                    "duration": None,
                    "width": None,
                    "height": None,
                    "frame_rate": None,
                    "thumbs": [],
                    "done": False,
                    "error": None,
                    "local_path": abs_p,
                })

        if on_progress:
            on_progress(len(state["videos"]), state["dirs"], dir_in)

    state["frontier"] = []
    state["visited"] = sorted(visited)
    return state


# --------------------------------------------------------------------------- #
# direct-link stand-in (LocalAPI mimics ThunderAPI.play_links)
# --------------------------------------------------------------------------- #
class LocalAPI:
    """Minimal ``api`` object understood by :func:`screenshots.process_video`.

    ``play_links(id)`` returns ``{"media": <absolute local path>, "info": {}}``;
    ffmpeg reads the file straight from disk, so nothing else is needed.
    """

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or util.load_config()
        self._cache = None

    def _by_id(self) -> dict:
        if self._cache is None:
            self._cache = {}
            for v in util.read_json(util.VIDEOS_FILE) or []:
                self._cache[v["id"]] = local_path_for(v)
        return self._cache

    def play_links(self, file_id: str) -> dict:
        p = self._by_id().get(file_id)
        if not p or not os.path.exists(p):
            raise RuntimeError(f"本地文件不存在（已删除？）: {p or file_id}")
        return {"media": p, "info": {}}

    def play_url(self, file_id: str, preference: str | None = None):
        links = self.play_links(file_id)
        return links["media"], {}


def api_factory():
    """Zero-arg factory used by ``screenshots.process_many`` in local mode."""
    return LocalAPI(util.load_config())


# --------------------------------------------------------------------------- #
# trash (reversible: move into a hidden folder on the same drive)
# --------------------------------------------------------------------------- #
def trash_for(root, abs_path: str) -> str:
    """Move a file/folder into the drive's trash dir, return new path."""
    src = Path(abs_path)
    dst_dir = Path(root).expanduser().resolve() / TRASH_DIR_NAME
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    k = 2
    while dst.exists():
        dst = dst_dir / f"{src.stem} ({k}){src.suffix}"
        k += 1
    util.log(f"移入回收站: {src} -> {dst}")
    shutil.move(str(src), str(dst))
    return str(dst)


# --------------------------------------------------------------------------- #
# organize (preview uses organize.build_plan; apply is filesystem-native)
# --------------------------------------------------------------------------- #
def base_folder(root) -> Path:
    """Real folder under ``root`` where organized videos land."""
    cfg = util.load_config()
    name = (cfg.get("organize_base") or "/整理").strip("/") or "整理"
    return Path(root).expanduser().resolve() / name


def build_local_plan(cfg: dict, move_cats=None, fix_inside: bool = False,
                     scope: str | None = None) -> dict:
    """Same shape as :func:`organize.load_and_build` but for local paths."""
    from . import categories, organize

    root = util.local_path()
    if not root:
        raise SystemExit("还没有选择要管理的硬盘/路径。请先运行: local path <路径>")

    files = util.read_json(util.FILES_FILE) or []
    source = "files.json" if files else "videos.json"
    if not files:
        files = util.read_json(util.VIDEOS_FILE) or []
    classified = util.read_json(util.CLASSIFIED_FILE) or []

    base = intern(base_folder(root))       # e.g. /E:/整理
    small = cfg.get("organize_small_mb", 20)
    large = cfg.get("organize_large_mb", 100)
    plan = organize.build_plan(files, classified, base=base, small_mb=small, large_mb=large,
                               move_cats=move_cats, fix_inside=fix_inside, scope=scope)
    plan["files_source"] = source
    plan["local_root"] = intern(root)
    return plan


def apply_local_plan(plan: dict, limit: int | None = None,
                     delete_folders: bool = False, progress=None, log=None) -> dict:
    """Execute a local organize plan with plain filesystem moves.

    Target folders are created on disk; files are moved; collisions renamed;
    emptied/junk source folders are moved into the drive trash (reversible).
    """
    from . import organize

    moves = plan["moves"]
    if limit:
        moves = moves[:limit]
    total = len(moves)
    done = 0
    results = []

    if progress:
        progress(0, total, plan.get("base") or "")

    by_target: dict = {}
    for m in moves:
        by_target.setdefault(m["to"], []).append(m)

    for to_in, arr in by_target.items():
        target_dir = extern(to_in)
        target_dir.mkdir(parents=True, exist_ok=True)
        for m in arr:
            src = extern(m["from"]).joinpath(m["name"])
            dst_name = m.get("target_name") or m["name"]
            dst = target_dir / dst_name
            ok, err = False, None
            try:
                if not src.exists():
                    raise FileNotFoundError(f"源文件不存在: {src}")
                k = 2
                while dst.exists():
                    stem, ext = _split_ext(dst_name)
                    dst = target_dir / f"{stem} ({k}){ext}"
                    k += 1
                shutil.move(str(src), str(dst))
                ok = True
            except Exception as exc:
                err = str(exc)
                if log:
                    log(f"移动失败 {src} -> {dst}: {err}", "ERROR")
            results.append({"id": m["id"], "name": m["name"], "to": to_in,
                            "ok": ok, "error": err})
            done += 1
            if progress:
                progress(done, total, to_in)
        if log:
            log(f"目录就绪并移动完成 {len(arr)} 个 -> {to_in}")

    deleted, delete_failed, kept, skipped = [], [], [], []
    if delete_folders and not limit:
        root = extern(plan.get("local_root") or "")
        for d in plan["delete_folders"]:
            folder_in = d["path"]
            name = folder_in.rstrip("/").split("/")[-1]
            if name in ORGANIZE_PROTECT or name in organize.PROTECT_NAMES:
                skipped.append(folder_in)
                continue
            folder = extern(folder_in)
            if not folder.exists():
                continue
            try:
                trash_for(root, str(folder))
                deleted.append(folder_in)
            except Exception as exc:
                delete_failed.append({"path": folder_in, "error": str(exc)})

    summary = {
        "moved_ok": sum(1 for r in results if r["ok"]),
        "moved_fail": sum(1 for r in results if not r["ok"]),
        "skipped_already": 0,
        "total_planned": len(plan["moves"]),
        "deleted_folders": deleted,
        "delete_failed": delete_failed,
        "skipped_system": skipped,
        "kept_now": kept,
        "limit": limit,
        "source": "local",
    }
    return {"results": results, "summary": summary}


ORGANIZE_PROTECT = {"整理"}


def _split_ext(name: str) -> tuple[str, str]:
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        return stem, "." + ext
    return name, ""


def clean_junk_folders(root, scope: str | None = None, log=None) -> dict:
    """Recursively trash folders that only contain junk (or nothing)."""
    from .organize import JUNK_EXTS

    files = util.read_json(util.FILES_FILE) or []
    if scope:
        top = scope if scope.startswith("/") else "/" + scope.strip("/")
        files = [f for f in files
                 if (f.get("path") or "") == top
                 or (f.get("path") or "").startswith(top + "/")]

    by_dir: dict = {}
    for f in files:
        d = f.get("path") or "/"
        if d not in by_dir:
            by_dir[d] = {"total": 0, "junk": 0}
        by_dir[d]["total"] += 1
        by_dir[d]["junk"] += 1 if _is_junk(f, JUNK_EXTS) else 0

    real_dirs: set = set()
    for p in by_dir:
        real_dirs.add(extern(p))
        q = p
        while "/" in q:
            q = q.rsplit("/", 1)[0]
            real_dirs.add(extern(q))

    root_p = Path(root).expanduser().resolve()
    deleted, failed = [], []
    for d in sorted(real_dirs, key=lambda d: len(str(d)), reverse=True):
        if d == root_p:
            continue
        if not d.exists() or not d.is_dir():
            continue
        if str(d).startswith(str(root_p / TRASH_DIR_NAME) + os.sep) or d == root_p / TRASH_DIR_NAME:
            continue
        if d == root_p / "整理":
            continue
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        if not entries:
            continue
        if all(not e.is_dir() and _is_junk_local(e.name, JUNK_EXTS) for e in entries):
            try:
                trash_for(root_p, str(d))
                deleted.append(str(d))
            except Exception as exc:
                failed.append({"path": str(d), "error": str(exc)})
    return {"deleted": deleted, "failed": failed}


def _is_junk_local(name: str, junk: set) -> bool:
    n = name.lower()
    return "." in n and n.rsplit(".", 1)[-1] in junk


def _is_junk(f: dict, junk: set) -> bool:
    size = int(f.get("size") or 0)
    if size >= 100 * 1024 * 1024:
        return False
    return _is_junk_local(f.get("name") or "", junk)