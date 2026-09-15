"""Harvest 迅雷 (Thunder) tokens from a debug Chrome, and refresh them.

We launch a dedicated Chrome instance (separate profile) with the DevTools
Protocol enabled, let the user log in once, then read ``localStorage`` and
intercept the ``captcha/init`` request.  The resulting token set is stored in
``data/tokens.json`` so later runs can work without the browser.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from typing import Callable, Optional

import requests

from . import util

CAPTCHA_INIT_URL = "https://xluser-ssl.xunlei.com/v1/shield/captcha/init"
TOKEN_URL = "https://xluser-ssl.xunlei.com/v1/auth/token"
PAN_URL = "https://pan.xunlei.com/?path=%2F"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

META_FIELDS = ("captcha_sign", "client_version", "package_name", "timestamp", "user_id")


class TokenError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Chrome + CDP
# --------------------------------------------------------------------------- #
def launch_chrome(cfg: dict, port: Optional[int] = None, restart: bool = False) -> Optional[subprocess.Popen]:
    """Start Chrome with remote debugging.  Returns the Popen, or None if a
    healthy instance is already listening on the port."""
    port = port or cfg["debug_port"]

    if restart:
        _kill_our_chrome(port)

    if _port_open(port):
        if _ensure_page(port, PAN_URL, create=True):
            util.log(f"调试端口 {port} 已在运行，复用现有 Chrome", "INFO")
            return None
        util.log("现有调试 Chrome 无可用页面且无法新建，尝试重启", "WARN")
        _kill_our_chrome(port)

    chrome = cfg["chrome_path"]
    if not os.path.exists(chrome):
        raise TokenError(f"找不到 Chrome: {chrome}（可在 config.json 中修改 chrome_path）")

    util.ensure_dirs()
    profile = str(util.CHROME_PROFILE)
    args = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
        PAN_URL,
    ]
    util.log(f"启动 Chrome（独立配置: {profile}）")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + 30
    while time.time() < deadline:
        if _port_open(port):
            return proc
        time.sleep(0.5)
    raise TokenError("Chrome 调试端口启动超时")


def _kill_our_chrome(port: int) -> None:
    """Kill only the debug Chrome that uses our dedicated profile."""
    profile = str(util.CHROME_PROFILE)
    try:
        subprocess.run(["pkill", "-f", profile], check=False)
    except Exception:
        pass
    deadline = time.time() + 10
    while time.time() < deadline and _port_open(port):
        time.sleep(0.5)
    if _port_open(port):
        util.log(f"端口 {port} 仍被占用，可手动结束该 Chrome 进程", "WARN")
    else:
        util.log("已关闭旧的调试 Chrome", "INFO")


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _browser_ws(port: int) -> str:
    resp = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=5)
    return resp.json()["webSocketDebuggerUrl"]


class CDP:
    def __init__(self, ws_url: str, event_handler: Callable[[dict], None] | None = None):
        try:
            import websocket  # provided by websocket-client
        except ImportError as exc:
            raise TokenError(
                "缺少 websocket-client。请用项目虚拟环境运行：\n"
                "  ./sweeper login --restart\n"
                "或先激活环境：source .venv/bin/activate 后用 python 运行。"
            ) from exc

        self._ws = websocket.create_connection(ws_url, timeout=10)
        self._ws.settimeout(2)
        self._id = 0
        self.event_handler = event_handler

    def call(self, method: str, params: dict | None = None, timeout: float = 20):
        from websocket import WebSocketTimeoutException

        self._id += 1
        mid = self._id
        self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = self._ws.recv()
            except WebSocketTimeoutException:
                continue
            except Exception as exc:  # connection closed etc.
                raise TokenError(f"CDP 连接中断: {exc}") from exc
            if not raw:
                continue
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise TokenError(f"CDP {method} 报错: {msg['error']}")
                return msg.get("result", {})
            self._dispatch(msg)
        raise TokenError(f"CDP {method} 超时")

    def pump(self, seconds: float) -> None:
        from websocket import WebSocketTimeoutException

        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                raw = self._ws.recv()
            except WebSocketTimeoutException:
                continue
            except Exception:
                return
            if raw:
                self._dispatch(json.loads(raw))

    def evaluate(self, expression: str):
        res = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return res.get("result", {}).get("value")

    def _dispatch(self, msg: dict) -> None:
        if "method" in msg and self.event_handler:
            try:
                self.event_handler(msg)
            except Exception:
                pass

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


def _ensure_page(port: int, url: str = PAN_URL, create: bool = True,
                 timeout: float = 20) -> Optional[dict]:
    """Return a usable page target, creating a new tab if needed."""
    last_exc = None
    deadline = time.time() + timeout
    tried_create = False
    while time.time() < deadline:
        try:
            targets = requests.get(f"http://127.0.0.1:{port}/json", timeout=5).json()
        except Exception as exc:
            last_exc = exc
            time.sleep(0.5)
            continue

        pages = [t for t in targets if t.get("type") == "page"]
        if pages:
            for t in pages:
                if "pan.xunlei.com" in (t.get("url") or ""):
                    return t
            return pages[0]

        if create and not tried_create:
            tried_create = True
            try:
                bws = _browser_ws(port)
                cdp = CDP(bws)
                cdp.call("Target.createTarget", {"url": url})
                cdp.close()
                util.log("现有 Chrome 无标签页，已新建一个", "INFO")
            except Exception as exc:
                last_exc = exc
        time.sleep(0.5)

    if not create and last_exc is None:
        return None
    if last_exc is not None:
        raise TokenError(f"没有找到可用的浏览器页面（{last_exc}）")
    return None


def _pick_page_target(port: int) -> dict:
    target = _ensure_page(port, PAN_URL, create=True, timeout=20)
    if not target:
        raise TokenError("没有找到可用的浏览器页面")
    return target


# --------------------------------------------------------------------------- #
# Harvest
# --------------------------------------------------------------------------- #
def harvest(cfg: dict, timeout: float = 600, restart: bool = False) -> dict:
    launch_chrome(cfg, restart=restart)
    port = cfg["debug_port"]

    deadline = time.time() + 30
    while time.time() < deadline and not _port_open(port):
        time.sleep(0.5)

    try:
        target = _pick_page_target(port)
    except TokenError:
        util.log("未能连上现有 Chrome，重启调试 Chrome 后重试", "WARN")
        launch_chrome(cfg, restart=True)
        time.sleep(1)
        target = _pick_page_target(port)
    captured: dict = {}

    def on_event(msg: dict) -> None:
        if msg.get("method") != "Network.requestWillBeSent":
            return
        req = msg.get("params", {}).get("request", {})
        if req.get("url") == CAPTCHA_INIT_URL and req.get("postData"):
            try:
                captured["meta"] = json.loads(req["postData"])
            except json.JSONDecodeError:
                pass

    cdp = CDP(target["webSocketDebuggerUrl"], on_event)
    try:
        cdp.call("Runtime.enable")
        cdp.call("Network.enable")
        cdp.call("Page.enable")
        if "pan.xunlei.com" not in (target.get("url") or ""):
            cdp.call("Page.navigate", {"url": PAN_URL})

        util.log("请在弹出的 Chrome 窗口中登录迅雷网盘（若已登录会自动继续）...")
        store: dict = {}
        deadline = time.time() + timeout
        while time.time() < deadline:
            cdp.pump(1.0)
            raw = cdp.evaluate(_LOCALSTORAGE_EXPR)
            if raw:
                try:
                    ls = json.loads(raw)
                except json.JSONDecodeError:
                    ls = {}
                if ls:
                    store = _build_store(ls, captured.get("meta"))
                    if store.get("credentials.access_token"):
                        break
            time.sleep(1)

        if not store.get("credentials.access_token"):
            raise TokenError("未获取到 access_token，请确认已成功登录迅雷网盘")

        store["_harvested_at"] = int(time.time())
        _dump_raw(store, ls)
        util.atomic_write_json(util.TOKENS_FILE, store)
        util.log(f"Token 已保存: {util.TOKENS_FILE}（access_token 已获取）", "INFO")
        return store
    finally:
        cdp.close()
        util.log("调试 Chrome 保持打开，可继续用于刷新 token（也可手动关闭）")


_LOCALSTORAGE_EXPR = (
    "(() => { const o = {}; for (let i = 0; i < localStorage.length; i++) {"
    " const k = localStorage.key(i); o[k] = localStorage.getItem(k); }"
    " return JSON.stringify(o); })()"
)


def _build_store(ls: dict, meta: Optional[dict]) -> dict:
    store: dict = {}

    for key, value in ls.items():
        low = key.lower()
        parsed = None
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed = None

        if low in ("deviceid", "device_id", "deviceid_sign"):
            store["device_id"] = value

        if isinstance(parsed, dict):
            # generic scan for known fields
            for field in ("client_id", "client_version", "package_name",
                          "captcha_sign", "user_id", "timestamp", "device_id"):
                if field in parsed and f"captcha.{field}" not in store:
                    store[f"captcha.{field}"] = parsed[field]

            if "captcha" in low:
                for k, v in parsed.items():
                    if isinstance(v, (str, int, float)):
                        store[f"captcha.{k}"] = v
            if "credentials" in low or "token" in low:
                for k in ("access_token", "refresh_token"):
                    if k in parsed:
                        store[f"credentials.{k}"] = parsed[k]

        if low == "deviceid":
            store["device_id"] = value

    if meta:
        if "client_id" in meta:
            store["captcha.client_id"] = meta["client_id"]
        if "device_id" in meta:
            store["captcha.device_id"] = meta["device_id"]
        meta_inner = meta.get("meta") if isinstance(meta.get("meta"), dict) else meta
        for field in META_FIELDS:
            if field in meta_inner:
                store[f"captcha.{field}"] = meta_inner[field]

    now = int(time.time())
    if store.get("captcha.token"):
        store["captcha.expires_at"] = now + 300
    if store.get("credentials.access_token"):
        store["credentials.expires_at"] = now + 43200
    return store


def _dump_raw(store: dict, ls: dict) -> None:
    store["_raw_localstorage_keys"] = sorted(ls.keys())


# --------------------------------------------------------------------------- #
# Refresh / token provider
# --------------------------------------------------------------------------- #
class TokenProvider:
    def __init__(self, store: dict, cfg: dict):
        self.store = store
        self.cfg = cfg
        self._chrome_refreshed = False
        self._lock = threading.RLock()
        self._last_refresh = 0.0

    # -- persistence ------------------------------------------------------- #
    def save(self) -> None:
        util.atomic_write_json(util.TOKENS_FILE, self.store)

    def get(self, key: str, default=None):
        return self.store.get(key, default)

    # -- access token ------------------------------------------------------ #
    def access_token(self) -> str:
        if self._valid("credentials.expires_at", 120) and self.get("credentials.access_token"):
            return self.store["credentials.access_token"]
        with self._lock:
            if not (self._valid("credentials.expires_at", 120) and self.get("credentials.access_token")):
                self._refresh_access_token()
            return self.store["credentials.access_token"]

    def _refresh_access_token(self) -> None:
        with self._lock:
            self._refresh_access_token_locked()

    def force_refresh(self) -> None:
        """Refresh after a 401, de-duplicated across threads (one refresh / 5s)."""
        with self._lock:
            if time.time() - self._last_refresh < 5:
                return
            self._refresh_access_token_locked()

    def _refresh_access_token_locked(self) -> None:
        client_id = self.get("captcha.client_id")
        refresh_token = self.get("credentials.refresh_token")
        if not client_id or not refresh_token:
            self._reread_from_chrome()
            client_id = self.get("captcha.client_id")
            refresh_token = self.get("credentials.refresh_token")
        if not client_id or not refresh_token:
            raise TokenError("缺少 client_id / refresh_token，请重新运行 login")

        util.log("access_token 过期，正在刷新...")
        url = TOKEN_URL
        payload = json.dumps({
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })
        headers = {
            "content-type": "application/json",
            "origin": "https://pan.xunlei.com",
            "referer": "https://pan.xunlei.com/",
            "user-agent": UA,
            "x-action": "401",
            "x-client-id": client_id,
            "x-device-id": self.get("captcha.device_id", ""),
            "x-device-sign": self.get("device_id", ""),
            "x-protocol-version": "301",
            "x-sdk-version": "3.4.20",
        }
        resp = requests.post(url, headers=headers, data=payload, timeout=self.cfg["request_timeout"])
        try:
            data = resp.json()
        except ValueError as exc:
            raise TokenError(f"刷新 token 响应异常: {resp.text[:200]}") from exc
        if "access_token" not in data:
            raise TokenError(f"刷新 token 失败: {data}")
        self.store["credentials.access_token"] = data["access_token"]
        if data.get("refresh_token"):
            self.store["credentials.refresh_token"] = data["refresh_token"]
        self.store["credentials.expires_at"] = int(time.time()) + int(data.get("expires_in", 43200))
        self._last_refresh = time.time()
        self.save()
        util.log("access_token 已刷新")

    def _reread_from_chrome(self) -> None:
        if self._chrome_refreshed:
            return
        self._chrome_refreshed = True
        try:
            fresh = reread_from_chrome(self.cfg)
        except Exception as exc:
            util.log(f"从 Chrome 重新读取 token 失败: {exc}", "WARN")
            return
        for k, v in fresh.items():
            if not k.startswith("_") and v:
                self.store[k] = v
        self.save()

    # -- captcha token ----------------------------------------------------- #
    # 迅雷的 captcha_token 是按「方法:路径」这个 action 绑定的，写操作
    # （移动/新建目录/改名/删除）必须用对应 action 申请，否则会 captcha_invalid。
    DEFAULT_ACTION = "get:/drive/v1/tasks"

    def _tokens(self) -> dict:
        return self.store.setdefault("captcha.tokens", {})

    def captcha_token(self, action: str | None = None) -> str:
        action = action or self.DEFAULT_ACTION
        cached = self._tokens().get(action) or {}
        if int(cached.get("expires_at", 0)) > int(time.time()) + 30 and cached.get("token"):
            return cached["token"]
        with self._lock:
            cached = self._tokens().get(action) or {}
            if int(cached.get("expires_at", 0)) > int(time.time()) + 30 and cached.get("token"):
                return cached["token"]
            return self._init_captcha(action)

    def invalidate_captcha(self, action: str | None = None) -> None:
        """Drop the cached token so the next call re-inits it."""
        self._tokens().pop(action or self.DEFAULT_ACTION, None)

    def _init_captcha(self, action: str | None = None) -> str:
        action = action or self.DEFAULT_ACTION
        client_id = self.get("captcha.client_id")
        device_id = self.get("captcha.device_id")
        if not client_id or not device_id:
            self._reread_from_chrome()
            client_id = self.get("captcha.client_id")
            device_id = self.get("captcha.device_id")
        if not client_id or not device_id:
            raise TokenError("缺少 client_id / device_id，无法获取 captcha_token，请重新 login")

        util.log(f"正在更新 captcha_token（action={action}）...")
        body = {
            "action": action,
            "client_id": client_id,
            "device_id": device_id,
            "meta": {
                "captcha_sign": self.get("captcha.captcha_sign", ""),
                "client_version": self.get("captcha.client_version", ""),
                "email": "",
                "package_name": self.get("captcha.package_name", ""),
                "phone_number": "",
                "timestamp": self.get("captcha.timestamp", ""),
                "user_id": self.get("captcha.user_id", ""),
                "username": "",
            },
        }
        headers = {
            "Content-Type": "text/plain;charset=UTF-8",
            "Origin": "https://pan.xunlei.com",
            "Referer": "https://pan.xunlei.com/",
            "User-Agent": UA,
        }
        resp = requests.post(CAPTCHA_INIT_URL, headers=headers, data=json.dumps(body),
                             timeout=self.cfg["request_timeout"])
        data = resp.json()
        if "captcha_token" not in data:
            raise TokenError(f"获取 captcha_token 失败（action={action}）: {data}")
        token = data["captcha_token"]
        self._tokens()[action] = {
            "token": token,
            "expires_at": int(time.time()) + int(data.get("expires_in", 300)),
        }
        # 兼容旧字段（默认 action）
        if action == self.DEFAULT_ACTION:
            self.store["captcha.token"] = token
            self.store["captcha.expires_at"] = self._tokens()[action]["expires_at"]
        self.save()
        util.log(f"captcha_token 已更新（action={action}）")
        return token

    def headers(self, action: str | None = None) -> dict:
        return {
            "Authorization": "Bearer " + self.access_token(),
            "Origin": "https://pan.xunlei.com",
            "Referer": "https://pan.xunlei.com/",
            "User-Agent": UA,
            "content-type": "application/json",
            "x-client-id": self.get("captcha.client_id", ""),
            "x-device-id": self.get("captcha.device_id", ""),
            "x-captcha-token": self.captcha_token(action),
        }

    def _valid(self, key: str, leeway: int) -> bool:
        try:
            return int(self.store.get(key, 0)) > int(time.time()) + leeway
        except (TypeError, ValueError):
            return False


def reread_from_chrome(cfg: dict) -> dict:
    """Re-read localStorage from an already running debug Chrome."""
    port = cfg["debug_port"]
    if not _port_open(port):
        raise TokenError("调试 Chrome 未在运行")
    target = _pick_page_target(port)
    cdp = CDP(target["webSocketDebuggerUrl"])
    try:
        cdp.call("Runtime.enable")
        raw = cdp.evaluate(_LOCALSTORAGE_EXPR)
        ls = json.loads(raw) if raw else {}
        store = _build_store(ls, None)
        now = int(time.time())
        if store.get("credentials.access_token"):
            store["credentials.expires_at"] = now + 43200
        if store.get("captcha.token"):
            store["captcha.expires_at"] = now + 300
        return store
    finally:
        cdp.close()


def load_provider(cfg: dict | None = None) -> TokenProvider:
    cfg = cfg or util.load_config()
    store = util.read_json(util.TOKENS_FILE)
    if not store:
        raise TokenError("尚未登录，请先运行: python -m thunder_sweeper login")
    return TokenProvider(store, cfg)
