"""Detect duplicate files: identical size + highly similar names (番号-aware).

Grouping rules (within the same byte size):
  * same 番号/code (e.g. ``KBR-033``)  -> duplicates regardless of the rest
  * high name similarity (difflib ratio on a normalized name) -> duplicates
"""

from __future__ import annotations

import collections
import difflib
import re

CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,6})[-_ ]?(\d{2,5})(?![0-9])")
TAG_RE = re.compile(r"[\[\(【（].*?[\]\)】）]")
EXT_RE = re.compile(r"\.[a-z0-9]{2,4}$")
DOMAIN_RE = re.compile(
    r"(?:www\.)?[a-z0-9][a-z0-9\-]*\.(?:com|net|org|xyz|cc|la|me|tv|info|top|vip|app|site|club|online)\b",
    re.I,
)

NOISE = {
    "mp4", "mkv", "avi", "mov", "wmv", "ts", "m2ts", "rmvb", "flv", "webm",
    "fhd", "hd", "sd", "4k", "8k", "1080p", "720p", "2160p", "1440p",
    "x264", "x265", "h264", "h265", "hevc", "aac", "10bit", "8bit",
    "uncensored", "leak", "leaked", "repack", "www", "com", "net", "la",
    "xyz", "cc", "org", "高清", "无码", "无碼", "破解", "中文字幕", "字幕",
    "中字", "完整版", "全集",
}


CODE_BLOCK = {
    "MP4", "MKV", "AVI", "MOV", "WMV", "HD", "FHD", "UHD", "SD", "XXX",
    "WEB", "RIP", "EP", "TV", "VR", "JAV", "MOVIE", "PART", "DISC", "CD",
    "DVD", "BD", "VLOG", "MV", "PV", "IMG", "VID", "FILE",
}


def extract_code(name: str) -> str | None:
    n = name or ""
    if "@" in n:
        n = n.split("@")[-1]
    n = DOMAIN_RE.sub(" ", n)
    m = CODE_RE.search(n.upper())
    if not m:
        return None
    prefix = m.group(1).upper()
    if prefix in CODE_BLOCK:
        return None
    return f"{prefix}-{m.group(2)}"


def normalize(name: str) -> str:
    n = (name or "").lower()
    if "@" in n:
        n = n.split("@")[-1]
    n = DOMAIN_RE.sub(" ", n)
    n = EXT_RE.sub(" ", n)
    n = TAG_RE.sub(" ", n)
    n = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", n)
    toks = [t for t in n.split() if t and t not in NOISE]
    return " ".join(toks)


def similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def find_duplicates(files: list[dict], sim_threshold: float = 0.85,
                    max_bucket: int = 400) -> list[dict]:
    """Return a list of duplicate groups (each a list of file dicts)."""
    by_size: dict = collections.defaultdict(list)
    for f in files:
        size = f.get("size")
        try:
            size = int(size)
        except (TypeError, ValueError):
            continue
        if size > 0:
            by_size[size].append(f)

    groups: list[list[dict]] = []
    assigned: set = set()
    for size, items in by_size.items():
        if len(items) < 2 or len(items) > max_bucket:
            continue
        names = [(f.get("name") or "", extract_code(f.get("name") or ""),
                  normalize(f.get("name") or "")) for f in items]
        used = [False] * len(items)
        for i in range(len(items)):
            if used[i]:
                continue
            cluster = [i]
            used[i] = True
            for j in range(i + 1, len(items)):
                if used[j]:
                    continue
                code_i, code_j = names[i][1], names[j][1]
                same_code = bool(code_i and code_j and code_i == code_j)
                sim = similarity(names[i][2], names[j][2])
                same_norm = bool(names[i][2]) and names[i][2] == names[j][2]
                if same_code or same_norm or sim >= sim_threshold:
                    cluster.append(j)
                    used[j] = True
            if len(cluster) > 1:
                group = [items[k] for k in cluster]
                groups.append(group)
                assigned.update(id(x) for x in group)

    # same code across slightly different sizes (only items not already grouped)
    by_code: dict = collections.defaultdict(list)
    for f in files:
        if id(f) in assigned:
            continue
        code = extract_code(f.get("name") or "")
        if code:
            by_code[code].append(f)
    for code, items in by_code.items():
        if len(items) >= 2:
            groups.append(items)
            assigned.update(id(x) for x in items)

    groups.sort(key=lambda g: (len(g), sum(int(x.get("size") or 0) for x in g)), reverse=True)
    return groups


def group_payload(groups: list[list[dict]]) -> list[dict]:
    out = []
    for idx, g in enumerate(groups):
        total = sum(int(x.get("size") or 0) for x in g)
        code = None
        for x in g:
            code = extract_code(x.get("name") or "")
            if code:
                break
        out.append({
            "index": idx,
            "code": code,
            "count": len(g),
            "size": int(g[0].get("size") or 0),
            "wasted": total - int(g[0].get("size") or 0),
            "items": [{
                "id": x.get("id"), "name": x.get("name"), "path": x.get("path"),
                "size": int(x.get("size") or 0), "category": x.get("category"),
            } for x in sorted(g, key=lambda y: int(y.get("size") or 0), reverse=True)],
        })
    return out
