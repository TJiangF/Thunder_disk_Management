"""User-editable category tree (supports sub-categories).

``data/categories.json`` is a list of nodes::

    [{"id": "jp", "name": "日本", "children": [{"id": "c....", "name": "无码"}]}, ...]

The six built-ins (jp/west/cn/adult_other/non_adult/unknown) are the default
tree.  Sub-categories nest arbitrarily; organise creates matching path folders.
"""

from __future__ import annotations

import copy
import uuid

from . import util

DEFAULT_TREE = [
    {"id": "jp", "name": "日本", "children": []},
    {"id": "west", "name": "欧美", "children": []},
    {"id": "cn", "name": "国产", "children": []},
    {"id": "adult_other", "name": "成人-其他", "children": []},
    {"id": "non_adult", "name": "非成人", "children": []},
    {"id": "unknown", "name": "未知", "children": []},
]

# never receive an organise move target
RESERVED = {"unknown", "non_adult"}


def default_tree() -> list:
    return copy.deepcopy(DEFAULT_TREE)


def load_tree() -> list:
    tree = util.read_json(util.CATEGORIES_FILE)
    if not isinstance(tree, list) or not tree:
        return default_tree()
    return tree


def save_tree(tree: list) -> None:
    util.atomic_write_json(util.CATEGORIES_FILE, tree)


def _walk(nodes, depth=0, parents=None, parent_id=None):
    parents = parents or []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        yield n, depth, parents, parent_id
        yield from _walk(n.get("children") or [], depth + 1, parents + [n.get("name")], n.get("id"))


def flat(tree: list | None = None) -> list:
    tree = tree if tree is not None else load_tree()
    out = []
    for n, depth, parents, parent_id in _walk(tree):
        out.append({
            "id": n.get("id"),
            "name": n.get("name"),
            "depth": depth,
            "parent": parent_id,
            "label": ("　" * depth) + (n.get("name") or ""),
            "path": parents + [n.get("name")],
            "children_count": len(n.get("children") or []),
        })
    return out


def labels(tree: list | None = None) -> dict:
    return {x["id"]: x["name"] for x in flat(tree)}


def ids(tree: list | None = None) -> set:
    return {x["id"] for x in flat(tree)}


def path_names(cid: str, tree: list | None = None) -> list | None:
    for x in flat(tree):
        if x["id"] == cid:
            return x["path"]
    return None


def _find(nodes, cid):
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("id") == cid:
            return n
        r = _find(n.get("children") or [], cid)
        if r:
            return r
    return None


def find(cid: str, tree: list | None = None):
    return _find(tree if tree is not None else load_tree(), cid)


def _new_id() -> str:
    return "c" + uuid.uuid4().hex[:8]


def add_node(parent_id: str | None, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("名称不能为空")
    tree = load_tree()
    node = {"id": _new_id(), "name": name, "children": []}
    if not parent_id:
        tree.append(node)
    else:
        parent = _find(tree, parent_id)
        if parent is None:
            raise ValueError("父分类不存在")
        parent.setdefault("children", []).append(node)
    save_tree(tree)
    return node


def rename_node(cid: str, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("名称不能为空")
    tree = load_tree()
    node = _find(tree, cid)
    if node is None:
        raise ValueError("分类不存在")
    node["name"] = name
    save_tree(tree)
    return node


def _collect(node) -> list:
    out = [node.get("id")]
    for c in node.get("children") or []:
        out.extend(_collect(c))
    return out


def delete_node(cid: str) -> list:
    tree = load_tree()
    removed: list = []

    def rm(nodes) -> bool:
        for i, n in enumerate(list(nodes)):
            if n.get("id") == cid:
                removed.extend(_collect(n))
                nodes.pop(i)
                return True
            if rm(n.get("children") or []):
                return True
        return False

    if not rm(tree):
        raise ValueError("分类不存在")
    save_tree(tree)
    return removed
