"""Build a cloud-drive re-organisation plan (preview only).

Move every adult video (日本/欧美/国产) into ``<base>/<分类>/`` and decide each
source folder's fate:

  * folder left empty (only videos)                    -> delete folder
  * folder only has torrents / small junk left         -> delete folder
  * folder still has a large / significant other file  -> keep original folder
"""

from __future__ import annotations

import collections
import time

from . import categories

JUNK_EXTS = {
    "torrent", "txt", "nfo", "url", "sfv", "md5", "sha1", "db", "ini", "cfg",
    "xml", "json", "log", "html", "htm", "jpg", "jpeg", "png", "gif", "webp",
    "bmp", "ico", "ass", "srt", "ssa", "sub", "idx", "cue", "zip", "part",
    "tmp", "apk", "exe", "bat", "cmd", "lnk", "scr", "msi", "ds_store",
}
MB = 1024 * 1024


def _ext(name: str) -> str:
    n = (name or "").lower()
    return n.rsplit(".", 1)[-1] if "." in n else ""


def _split_ext(name: str) -> tuple[str, str]:
    n = name or ""
    if "." in n:
        stem, ext = n.rsplit(".", 1)
        return stem, "." + ext
    return n, ""


def is_significant(f: dict, small_mb: float, large_mb: float) -> bool:
    size = int(f.get("size") or 0)
    if size >= large_mb * MB:
        return True
    if _ext(f.get("name")) in JUNK_EXTS:
        return False
    return size >= small_mb * MB


def build_plan(files: list[dict], classified: list[dict], base: str = "/整理",
               small_mb: float = 20, large_mb: float = 100,
               move_cats: set | None = None, fix_inside: bool = False,
               scope: str | None = None) -> dict:
    scope = ("/" + scope.strip("/")) if scope and scope.strip("/") else None
    if scope:
        files = [f for f in files
                 if (f.get("path") or "/") == scope
                 or (f.get("path") or "/").startswith(scope + "/")]
    cat = {c.get("id"): c.get("category") for c in (classified or [])}
    tree = categories.load_tree()
    paths = {x["id"]: x["path"] for x in categories.flat(tree)}
    if move_cats is None:
        move_cats = set(paths) - categories.RESERVED
    move_cats = set(move_cats)
    base = "/" + base.strip("/") if base.strip("/") else "/整理"

    by_folder: dict = collections.defaultdict(list)
    for f in files:
        by_folder[f.get("path") or "/"].append(f)

    under_base = lambda p: (p or "/") == base or (p or "/").startswith(base + "/")

    used_names: dict = collections.defaultdict(set)
    for f in files:
        p = f.get("path") or "/"
        if under_base(p):
            used_names[p].add(f.get("name"))

    moves = []
    moved_ids = set()
    mismatches = []          # files already inside base whose path != their category
    for f in sorted(files, key=lambda x: int(x.get("size") or 0), reverse=True):
        if f.get("kind") == "drive#folder":
            continue
        if f.get("is_video", True) is False:
            continue  # only move actual videos
        if f.get("id") in moved_ids:
            continue
        c = cat.get(f.get("id"))
        if c not in move_cats:
            continue
        names = paths.get(c)
        if not names:
            continue
        src = f.get("path") or "/"
        target = f"{base}/{'/'.join(names)}"
        in_place = (src == target) or src.startswith(target + "/")
        if src.startswith(base + "/") or src == base:
            if not in_place:
                mismatches.append({
                    "id": f.get("id"), "name": f.get("name"), "from": src, "to": target,
                    "size": int(f.get("size") or 0), "category": c,
                })
            if not in_place and fix_inside:
                pass  # fall through to build a move
            else:
                continue
        elif in_place:
            continue
        name = f.get("name") or f.get("id")
        stem, ext = _split_ext(name)
        cand, k = name, 2
        while cand in used_names[target]:
            cand = f"{stem} ({k}){ext}"
            k += 1
        used_names[target].add(cand)
        moves.append({
            "id": f.get("id"), "name": name, "target_name": cand,
            "from": src, "to": target, "size": int(f.get("size") or 0), "category": c,
        })
        moved_ids.add(f.get("id"))

    moves_by_folder: dict = collections.defaultdict(list)
    for m in moves:
        moves_by_folder[m["from"]].append(m)

    delete_folders, keep_folders = [], []
    for folder, mv in moves_by_folder.items():
        if folder == "/":
            continue  # root never deleted
        remaining = [f for f in by_folder.get(folder, []) if f.get("id") not in moved_ids]
        significant = [f for f in remaining if is_significant(f, small_mb, large_mb)]
        if significant:
            keep_folders.append({
                "path": folder,
                "moved": len(mv),
                "kept_files": [{"name": f.get("name"), "size": int(f.get("size") or 0)}
                               for f in sorted(significant, key=lambda x: int(x.get("size") or 0),
                                               reverse=True)[:20]],
                "kept_count": len(significant),
            })
        else:
            delete_folders.append({
                "path": folder,
                "moved": len(mv),
                "extra_removed": len(remaining),
                "extra_size": sum(int(f.get("size") or 0) for f in remaining),
                "extra": [{"name": f.get("name"), "size": int(f.get("size") or 0)}
                          for f in sorted(remaining, key=lambda x: int(x.get("size") or 0),
                                          reverse=True)[:20]],
            })

    # merge multiple source folders that map to the same target for readability
    by_target: dict = collections.defaultdict(list)
    for m in moves:
        by_target[m["to"]].append(m)

    delete_folders.sort(key=lambda d: (d["moved"] + d["extra_removed"]), reverse=True)
    keep_folders.sort(key=lambda d: d["moved"], reverse=True)

    return {
        "base": base,
        "scope": scope,
        "moves": moves,
        "mismatches": mismatches,
        "by_target": {k: v for k, v in sorted(by_target.items())},
        "delete_folders": delete_folders,
        "keep_folders": keep_folders,
        "summary": {
            "move_count": len(moves),
            "move_size": sum(m["size"] for m in moves),
            "targets": {k: v for k, v in sorted(collections.Counter(
                m["to"] for m in moves).items())},
            "delete_folder_count": len(delete_folders),
            "delete_extra_count": sum(d["extra_removed"] for d in delete_folders),
            "delete_extra_size": sum(d["extra_size"] for d in delete_folders),
            "keep_folder_count": len(keep_folders),
            "mismatch_count": len(mismatches),
            "mismatch_size": sum(m["size"] for m in mismatches),
            "small_mb": small_mb, "large_mb": large_mb,
        },
    }


def load_and_build(cfg: dict, move_cats: tuple | None = None, fix_inside: bool = False,
                   scope: str | None = None) -> dict:
    from . import util

    files = util.read_json(util.FILES_FILE) or []
    source = "files.json" if files else "videos.json"
    if not files:
        files = util.read_json(util.VIDEOS_FILE) or []
    classified = util.read_json(util.CLASSIFIED_FILE) or []
    base = cfg.get("organize_base", "/整理")
    small = cfg.get("organize_small_mb", 20)
    large = cfg.get("organize_large_mb", 100)
    plan = build_plan(files, classified, base=base, small_mb=small, large_mb=large,
                      move_cats=move_cats, fix_inside=fix_inside, scope=scope)
    plan["files_source"] = source
    return plan


def folder_tree(files: list[dict]) -> list[dict]:
    """Nested cloud-folder tree (name/path/children/count) for scope selection.

    Built from the ``path`` field (the folder containing each file), so it lists
    every folder that the scan saw.  ``count`` is the number of files within.
    """
    direct: dict = collections.Counter()
    paths: set = set()
    for f in files:
        raw = (f.get("path") or "").strip("/")
        p = "/" + raw if raw else "/"
        direct[p] += 1
        while p != "/":
            paths.add(p)
            p = p.rsplit("/", 1)[0] or "/"

    def count_under(path: str) -> int:
        return sum(c for q, c in direct.items()
                   if q == path or q.startswith(path + "/"))

    def build(prefix: str) -> list[dict]:
        pre = "/" if prefix == "/" else prefix + "/"
        seen: set = set()
        out = []
        for q in sorted(paths):
            if not q.startswith(pre):
                continue
            seg = q[len(pre):].split("/", 1)[0]
            child = "/" + seg if pre == "/" else pre + seg
            if child in seen:
                continue
            seen.add(child)
            out.append({"name": seg, "path": child,
                        "children": build(child), "count": count_under(child)})
        return out

    return build("/")


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #
class Folders:
    """Resolve (and optionally create) folder ids from a path, with caching."""

    def __init__(self, api):
        self.api = api
        self.cache = {(): ""}          # segs tuple -> folder id (root = "")
        self.children: dict = {}       # parent_id -> {name: id}

    def _children(self, parent_id: str) -> dict:
        if parent_id not in self.children:
            mapping = {}
            for entry in self.api.list_folder(parent_id):
                if entry.get("kind") == "drive#folder":
                    mapping[entry.get("name")] = entry.get("id")
            self.children[parent_id] = mapping
        return self.children[parent_id]

    def id_of(self, path: str, create: bool = False):
        segs = tuple(s for s in (path or "/").split("/") if s)
        parent = ""
        for i in range(len(segs)):
            key = segs[:i + 1]
            if key in self.cache:
                parent = self.cache[key]
                continue
            name = segs[i]
            children = self._children(parent)
            if name in children:
                nid = children[name]
            elif create:
                nid = self.api.create_folder(name, parent)
                children[name] = nid
            else:
                return None
            self.cache[key] = nid
            parent = nid
        return parent


def _is_system_error(msg: str) -> bool:
    return "file_operate_system_folder" in (msg or "") or "系统默认文件夹" in (msg or "")


def _batch_move(api, ids, parent_id, attempts: int = 4, wait: bool = True):
    """Move one chunk; retry on task-limit with backoff; optionally wait for the
    async task to finish so we never pile up tasks. Returns (ok, err)."""
    delay = 1.5
    last = None
    for attempt in range(1, attempts + 1):
        try:
            resp = api.batch_move(ids, parent_id)
        except Exception as exc:
            last = str(exc)
            if "task_run_nums_limit" in last or "429" in last or "too many" in last:
                time.sleep(delay)
                delay = min(delay * 2, 20)
                continue
            return False, last
        task_id = (resp or {}).get("task_id")
        if wait and task_id:
            _wait_task(api, task_id)
        return True, None
    return False, last


def _wait_task(api, task_id: str, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            task = api.get_task(task_id)
        except Exception:
            time.sleep(1)
            continue
        phase = (task.get("phase") or "").upper()
        if not task:
            return
        if "COMPLETE" in phase or "ERROR" in phase or "FAIL" in phase:
            return
        time.sleep(0.6)


def apply_plan(api, plan: dict, limit: int | None = None, delete_folders: bool = False,
               chunk: int = 8, delay: float = 1.0, progress=None, log=None) -> dict:
    """Execute the plan.  ``limit`` applies only to moves (for testing); folder
    deletion is skipped entirely when ``limit`` is set (safer)."""
    import collections

    def say(msg, level="INFO"):
        if log:
            log(msg, level)

    folders = Folders(api)
    base = plan["base"]
    targets = sorted({m["to"] for m in plan["moves"]})
    target_ids = {}
    already = set()  # info only: files currently sitting in a target folder
    for tpath in targets:
        tid = folders.id_of(tpath, create=True)
        target_ids[tpath] = tid
        if tid:
            for e in api.list_folder(tid):
                if e.get("kind") == "drive#file":
                    already.add(e.get("id"))
    say(f"目标目录已就绪：{len(targets)} 个；目标目录内现有文件 {len(already)} 个")

    moves = plan["moves"]
    if limit:
        moves = moves[:limit]
    total = len(moves)

    by_target = collections.defaultdict(list)
    for m in moves:
        by_target[m["to"]].append(m)

    results = []
    done = 0
    chunk = max(1, int(chunk))
    for to_path, arr in by_target.items():
        tid = target_ids.get(to_path) or folders.id_of(to_path, create=True)
        for start in range(0, len(arr), chunk):
            part = arr[start:start + chunk]
            ids = [m["id"] for m in part]
            ok, err = _batch_move(api, ids, tid)
            if not ok and err and "file_move_or_copy_to_cur" not in err:
                # chunk failed as a whole -> retry one by one to isolate
                say(f"整批失败，改为逐个重试（{len(ids)} 个 → {to_path}）", "WARN")
                ok_map = {}
                for m in part:
                    o, e = _batch_move(api, [m["id"]], tid, attempts=2)
                    ok_map[m["id"]] = (o, e)
                    time.sleep(delay)
                for m in part:
                    o, e = ok_map[m["id"]]
                    results.append({"id": m["id"], "name": m["name"], "to": to_path,
                                    "ok": o, "error": e})
                done += len(part)
                if progress:
                    progress(done, total, to_path)
                continue
            for m in part:
                results.append({"id": m["id"], "name": m["name"], "to": to_path,
                                "ok": ok, "error": err})
            if not ok:
                say(f"移动失败（{len(ids)} 个 → {to_path}）: {err}", "ERROR")
            done += len(part)
            if progress:
                progress(done, total, to_path)
            time.sleep(delay)

    # rename files whose target name had to change to avoid collisions
    ok_ids = {r["id"] for r in results if r["ok"]}
    for m in moves:
        if m["id"] in ok_ids and m.get("target_name") and m["target_name"] != m["name"]:
            try:
                api.rename(m["id"], m["target_name"])
            except Exception as exc:
                say(f"改名失败 {m['name']} → {m['target_name']}: {exc}", "WARN")

    deleted, delete_failed, kept_now, skipped_system = [], [], [], []
    if delete_folders and not limit:
        small_mb = plan["summary"].get("small_mb", 20)
        large_mb = plan["summary"].get("large_mb", 100)
        for d in plan["delete_folders"]:
            folder = d["path"]
            if folder.rstrip("/").split("/")[-1] in PROTECT_NAMES:
                skipped_system.append(folder)
                continue
            fid = folders.id_of(folder, create=False)
            if not fid:
                continue
            try:
                entries = api.list_folder(fid)
            except Exception as exc:
                delete_failed.append({"path": folder, "error": str(exc)})
                continue
            if any(e.get("kind") == "drive#folder" for e in entries):
                kept_now.append(folder)  # has subfolders, keep
                continue
            remaining = [e for e in entries if e.get("kind") == "drive#file"]
            significant = [is_significant(e, small_mb, large_mb) for e in remaining]
            if any(significant):
                kept_now.append(folder)  # still has large files, keep
                continue
            try:
                api.trash(fid)
                deleted.append(folder)
                say(f"已删除文件夹 {folder}")
            except Exception as exc:
                if _is_system_error(str(exc)):
                    skipped_system.append(folder)
                    say(f"系统文件夹，跳过 {folder}", "WARN")
                else:
                    delete_failed.append({"path": folder, "error": str(exc)})
                    say(f"删除文件夹失败 {folder}: {exc}", "ERROR")
            time.sleep(0.3)

    summary = {
        "moved_ok": len(ok_ids),
        "moved_fail": sum(1 for r in results if not r["ok"]),
        "skipped_already": len(already),
        "total_planned": len(plan["moves"]),
        "deleted_folders": deleted,
        "delete_failed": delete_failed,
        "skipped_system": skipped_system,
        "kept_now": kept_now,
        "limit": limit,
        "delete_folders_flag": delete_folders,
    }
    return {"results": results, "summary": summary}


PROTECT_NAMES = {"超级保险箱", "在线解压", "整理", "我的转存"}


def clean_junk_folders(api, log=None, protect: set | None = None,
                       scope: str | None = None) -> dict:
    """Recursively delete folders that contain only junk files (apk/html/txt/…)
    or are empty.  Post-order: children first, then parents.

    A folder is kept if it still has a non-junk file or any subfolder.
    ``scope`` limits the walk to one cloud path (its children); the scope folder
    itself is never deleted.
    """
    protect = protect or PROTECT_NAMES
    deleted, failed = [], []
    scope = ("/" + scope.strip("/")) if scope and scope.strip("/") else None

    def say(msg, level="INFO"):
        if log:
            log(msg, level)

    def junk_file(e) -> bool:
        return _ext(e.get("name")) in JUNK_EXTS

    def walk(fid: str, path: str) -> bool:
        try:
            entries = api.list_folder(fid)
        except Exception as exc:
            say(f"列目录失败 {path}: {exc}", "WARN")
            return False
        for f in [e for e in entries if e.get("kind") == "drive#folder"]:
            if f.get("name") in protect:
                continue
            walk(f["id"], f"{path.rstrip('/')}/{f.get('name')}")
            time.sleep(0.1)
        try:
            entries = api.list_folder(fid)
        except Exception:
            return False
        remaining = [e for e in entries
                     if e.get("kind") == "drive#folder" or not junk_file(e)]
        if remaining:
            return False
        try:
            api.trash(fid)
            deleted.append(path)
            say(f"删除垃圾文件夹 {path}")
            return True
        except Exception as exc:
            if _is_system_error(str(exc)):
                say(f"系统文件夹，跳过 {path}", "WARN")
                return False
            failed.append({"path": path, "error": str(exc)})
            say(f"删除失败 {path}: {exc}", "ERROR")
            return False

    if scope:
        start_id = Folders(api).id_of(scope, create=False)
        if not start_id:
            say(f"作用路径不存在，跳过垃圾清理: {scope}", "WARN")
            return {"deleted": deleted, "failed": failed}
        say(f"仅清理作用路径内的垃圾文件夹: {scope}")
        try:
            entries = api.list_folder(start_id)
        except Exception as exc:
            say(f"列目录失败 {scope}: {exc}", "WARN")
            entries = []
        base = scope.rstrip("/")
        for f in [e for e in entries if e.get("kind") == "drive#folder"]:
            if f.get("name") in protect:
                continue
            walk(f["id"], f"{base}/{f.get('name')}")
            time.sleep(0.1)
        return {"deleted": deleted, "failed": failed}

    root = api.list_folder("")
    for f in [e for e in root if e.get("kind") == "drive#folder"]:
        if f.get("name") in protect:
            continue
        walk(f["id"], f"/{f.get('name')}")
        time.sleep(0.1)

    return {"deleted": deleted, "failed": failed}


