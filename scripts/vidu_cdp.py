# -*- coding: utf-8 -*-
"""
vidu_cdp.py —— Vidu 平台渠道的最小 CDP 客户端（零第三方依赖，仅需 websocket-client）。

职责单一：把浏览器当成一个可执行 JS 的远程终端。
- 连上已开启 --remote-debugging-port 的 Edge
- eval(js) 执行任意 JS 并取回 JSON 结果（自带 await / 错误回传）
- screenshot() 存图
- wait_for(js) 轮询直到条件为真

★ 本模块不含任何业务逻辑（不认"图片/音频/创作"），业务在 vidu_ops.py。
★ 调用方必须传显式端口；不读环境变量，不猜路径。
"""
from __future__ import annotations

import base64
import json
import time
import urllib.request

import websocket  # websocket-client

DEFAULT_TIMEOUT = 20.0


def _http_json(port: int, path: str, timeout: float = 5.0):
    """直连 127.0.0.1，绕开系统代理（本机 curl/requests 默认走代理会误判端口未开）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = "http://127.0.0.1:%d%s" % (port, path)
    with opener.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def list_targets(port: int = 9222):
    return _http_json(port, "/json/list")


def find_page(port: int = 9222, url_contains: str = "vidu.cn", timeout: float = 15.0):
    """轮询找到第一个匹配的 page target。返回 (ws_url, target_dict)。"""
    deadline = time.time() + timeout
    last = []
    while time.time() < deadline:
        try:
            last = list_targets(port)
        except Exception:
            time.sleep(0.5)
            continue
        for t in last:
            if t.get("type") == "page" and url_contains in (t.get("url") or ""):
                ws = t.get("webSocketDebuggerUrl")
                if ws:
                    return ws, t
        time.sleep(0.5)
    raise RuntimeError("未找到匹配 page（url 含 %r）。当前 target 数=%d，示例=%s"
                       % (url_contains, len(last), [x.get("url") for x in last[:5]]))


class CDP:
    """极简 CDP 会话。只实现 Runtime / Page 里真正用到的几条命令。"""

    def __init__(self, port: int = 9222, url_contains: str = "vidu.cn",
                 timeout: float = DEFAULT_TIMEOUT, verbose: bool = False):
        self.port = port
        self.timeout = timeout
        self.verbose = verbose
        ws_url, target = find_page(port, url_contains)
        self.target = target
        self.ws = websocket.create_connection(ws_url, timeout=timeout,
                                              suppress_origin=True,
                                              enable_multithread=True)
        self._id = 0

    # ---------- 底层 ----------
    def send(self, method: str, params: dict | None = None, timeout: float | None = None):
        self._id += 1
        mid = self._id
        payload = {"id": mid, "method": method}
        if params:
            payload["params"] = params
        if self.verbose:
            print("[CDP >]", method, json.dumps(params or {}, ensure_ascii=False)[:200])
        self.ws.send(json.dumps(payload))
        deadline = time.time() + (timeout or self.timeout)
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise TimeoutError("CDP 超时: %s" % method)
            self.ws.settimeout(min(remain, 5.0))
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                continue
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("CDP error %s: %s" % (method, msg["error"]))
                return msg.get("result", {})

    # ---------- 常用 ----------
    def eval(self, js: str, await_promise: bool = True, timeout: float | None = None):
        """执行 JS 表达式并返回其值（值须可 JSON 序列化）。异常会原样抛回 Python。"""
        r = self.send("Runtime.evaluate", {
            "expression": js,
            "returnByValue": True,
            "awaitPromise": await_promise,
            "userGesture": True,
        }, timeout=timeout)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise RuntimeError("JS 异常: %s" % json.dumps(d, ensure_ascii=False)[:600])
        return r.get("result", {}).get("value")

    def wait_for(self, js: str, timeout: float = 20.0, interval: float = 0.3,
                 desc: str = ""):
        """轮询直到 js 返回真值，返回该值；超时抛错。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            v = self.eval(js)
            if v:
                return v
            time.sleep(interval)
        raise TimeoutError("wait_for 超时 %ss: %s" % (timeout, desc or js[:120]))

    def screenshot(self, path: str, full_page: bool = False):
        params = {"format": "png"}
        if full_page:
            m = self.send("Page.getLayoutMetrics")
            css = m.get("cssContentSize") or m.get("contentSize") or {}
            params["captureBeyondViewport"] = True
            params["clip"] = {"x": 0, "y": 0, "width": css.get("width", 1440),
                              "height": css.get("height", 900), "scale": 1}
        r = self.send("Page.captureScreenshot", params)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return path

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


# ---------- 供命令行自检 ----------
if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9222
    with CDP(port=port, verbose=True) as c:
        print("target:", c.target.get("url"))
        print("title:", c.eval("document.title"))
        print("readyState:", c.eval("document.readyState"))
