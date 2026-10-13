# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:146e215dba · 溯源标记，请保留
"""vidu_submit.py —— 点「创作」并**用网络确认**（本渠道最容易白跑的一步）。

★★ 实测（2026-10-07）：改完设置后平台会先算积分，期间按钮 `aria-disabled=true`；
   此时用真实鼠标点下去**静默无效** —— 返回 200、零 POST、页面无报错、表单不 reset。
   ⇒ 必须「等就绪 → 点 → 网络确认 → 重试」。

★★ 为什么必须旁路监听：提交成功后**表单不清空**，没有任何稳定的成功 DOM 标记 ——
   只有网络层的 `POST /vidu/v1/tasks` 能证明"真的提交了"。
"""
from __future__ import annotations

import json
import threading
import time

import websocket

from vidu_cdp import find_page
from vidu_js import JS_SUBMIT
from vidu_log import log
from vidu_ops import mouse_click

class NetWatch:
    """★ 旁路 CDP 连接：只监听 `POST /vidu/v1/tasks`。

    为什么要它：点「创作」后**只有网络层知道到底提交没提交** ——
    页面会保留表单（不 reset），也没有稳定的成功 DOM 标记。实测踩过：
    改完设置后平台会先请求 `/vidu/v1/tasks/credits` 校验（按钮 `aria-disabled=true`），
    此时点下去**静默无效**（返回 200 但没有任何 POST）。靠网络才能判定。
    """

    MARK = "/vidu/v1/tasks"

    def __init__(self, port=9222, url_contains="vidu.cn"):
        self.port = port
        self.url_contains = url_contains
        self.hits = []
        self._stop = False
        self._th = None
        self._ws = None

    def start(self):
        try:
            ws_url, _ = find_page(self.port, self.url_contains)
            self._ws = websocket.create_connection(ws_url, timeout=5, suppress_origin=True)
            self._ws.send(json.dumps({"id": 1, "method": "Network.enable",
                                      "params": {"maxPostDataSize": 2000000}}))
        except Exception as e:
            self._ws = None
            log({"warn": "NetWatch 启动失败", "err": "%s: %s" % (type(e).__name__, e)})
            return
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def _loop(self):
        self._ws.settimeout(1.0)
        while not self._stop:
            try:
                raw = self._ws.recv()
            except Exception:
                continue
            if not raw:
                continue
            try:
                m = json.loads(raw)
            except Exception:
                continue
            if m.get("method") == "Network.requestWillBeSent":
                req = m.get("params", {}).get("request", {})
                u = req.get("url", "")
                if self.MARK in u and req.get("method") == "POST":
                    self.hits.append({"url": u[:160], "len": len(req.get("postData") or ""),
                                      "t": time.time()})

    def count(self):
        return len(self.hits)

    def stop(self):
        self._stop = True
        if self._th:
            self._th.join(timeout=2)
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

def wait_submit_ready(c, timeout=25.0):
    """等「创作」按钮从 aria-disabled 恢复可点（平台在算积分时禁用）。"""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        st = c.eval(JS_SUBMIT) or {}
        last = st
        if not st.get("err") and st.get("ariaDisabled") in (None, "false") and not st.get("disabled"):
            return st
        time.sleep(1.0)
    return last or {}

def click_submit(c, watch=None, tries=3):
    """点「创作」，并用网络确认**真的提交了**；没提交就重试。

    实测（2026-10-07）：`aria-disabled=true` 时点击**静默无效**，
    而 `apply_settings` 刚改完宽高比就会触发积分校验 ⇒ 必须"等就绪再点 + 网络复核"。
    """
    detail = []
    for i in range(tries):
        st = wait_submit_ready(c, timeout=25.0 if i == 0 else 12.0)
        if st.get("err"):
            return {"ok": False, "why": st, "detail": detail}
        n0 = watch.count() if watch else None
        mouse_click(c, st["x"], st["y"])
        # 等网络层确认（最多 20s）
        t0 = time.time()
        while time.time() - t0 < 20:
            time.sleep(0.5)
            if watch and watch.count() > n0:
                return {"ok": True, "verified": True, "try": i + 1, "at": [st["x"], st["y"]],
                        "text": st.get("text"), "ariaDisabled": st.get("ariaDisabled"),
                        "posts": watch.hits[n0:], "detail": detail}
            if watch is None:
                time.sleep(2.5)
                return {"ok": True, "verified": False, "try": i + 1, "at": [st["x"], st["y"]],
                        "text": st.get("text"), "ariaDisabled": st.get("ariaDisabled")}
        detail.append({"try": i + 1, "ariaDisabled": st.get("ariaDisabled"),
                       "disabled": st.get("disabled"), "posts": (watch.count() - n0) if watch else None})
        time.sleep(1.5)
    return {"ok": False, "verified": False, "why": "点了 %d 次都没看到 POST /vidu/v1/tasks" % tries,
            "detail": detail}
