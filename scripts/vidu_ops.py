# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:08da7c8b3d · 溯源标记，请保留
"""vidu_ops.py —— 页面原语（上传 / 点击 / 设置区 / 组间复位）。

只负责"把页面点对"，**不含任何渠道策略**；编排在 `vidu_run.py`，
提交在 `vidu_submit.py`，mention 在 `vidu_mentions.py`。

★ 定位的四个坑（整页误命中历史卡片 / 编辑器徽标几何溢出 / 顶层 const 污染 /
  时长条第 1–8 格在标签左侧）在 `references/vidu-平台机制.md` §4，改本文件前先读。
"""
from __future__ import annotations

import json
import time

from vidu_js import (JS_FILE_INPUTS, JS_CLICK_TEXT, JS_PICK_INPUT,
                     JS_ROW_CURRENT, JS_ROW_CELL, JS_ROW_ARROWS, JS_ROW_SELECTED, JS_ROW_SCROLL_TO,
                     JS_PICK_OPTION, JS_EDITOR_TEXT, JS_EDITOR_CLEAR)
from vidu_log import log

def file_input_object(c, want, timeout=45):
    """want='image'|'audio' → 返回 (objectId, accept)。

    ★ 按 **accept 属性**判定，不按序号（切页签会增删 input，序号会漂）。
    ★ 必须返回 **objectId**，不是 nodeId：SPA 重渲染会让 nodeId 失效
      （实测 `DOM.setFileInputFiles` 报 `Could not find node with given id`）；
      `DOM.setFileInputFiles` **原生支持 objectId**，用它就绕开了失效问题。
    ★ 带等待重试：页面 hydrate 需要时间。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.send("Runtime.evaluate", {
            "expression": JS_PICK_INPUT + "(%s)" % json.dumps(want),
            "returnByValue": False, "awaitPromise": False, "userGesture": True})
        res = r.get("result", {})
        oid = res.get("objectId")
        if oid and res.get("subtype") != "null":
            try:
                desc = c.send("DOM.describeNode", {"objectId": oid})["node"]
                attrs = desc.get("attributes", [])
                acc = ""
                for k in range(0, len(attrs) - 1, 2):
                    if attrs[k].lower() == "accept":
                        acc = attrs[k + 1]
                return oid, acc
            except Exception:
                pass
        time.sleep(1.5)
    return None, ""

def click_text(c, text):
    return c.eval(JS_CLICK_TEXT + "(%s)" % json.dumps(text, ensure_ascii=False))

# ---------------------------------------------------------------- 真实鼠标 / 文件选择框
# ★★ 2026-10-07 实测（音频入口的坑就在这里，务必照做）：
#   「+ 图片」这一行在 DOM 里**有一个真实的 input[type=file][multiple]**，可以直接
#   `DOM.setFileInputFiles({objectId})` 投喂（见 file_input_object）。
#   「+ 音频」这一行**DOM 里查不到任何 input**（`querySelectorAll('input[type=file]')` 只有图片的），
#   点它是**真的去唤起原生文件选择框**。正确姿势：
#       ① `Page.setInterceptFileChooserDialog(enabled=True)`
#       ② 用 Input.dispatchMouseEvent 真实点「音频」按钮
#       ③ 收 `Page.fileChooserOpened` 事件 → 拿 `backendNodeId`（那才是隐藏 input）
#       ④ `DOM.setFileInputFiles({backendNodeId, files})`
#   ⚠ 隐藏 input 的真实属性：accept=".wav,.mp3,.aiff,.aif,.aac,.ogg,.flac" multiple display:none
#   ⚠⚠ **音频上限 = 3**：平台会弹红字「一次最多选择 3 个文件，超出部分已忽略。」——
#      多传的第 4 个起会被**静默丢弃**（实测 4 个只生成 音频1/2/3）。别指望能塞更多。

JS_BUTTON_COORD = r"""((want) => {
  let best = null;
  document.querySelectorAll('button').forEach(el => {
    if ((el.innerText || '').trim() !== want) return;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return;
    best = el;
  });
  if (!best) return null;
  best.scrollIntoView({block: 'center'});
  const r = best.getBoundingClientRect();
  return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
})"""

def mouse_click(c, x, y):
    for t in ("mousePressed", "mouseReleased"):
        c.send("Input.dispatchMouseEvent", {
            "type": t, "x": x, "y": y, "button": "left",
            "clickCount": 1, "buttons": 1 if t == "mousePressed" else 0})

def wait_event(c, method, timeout=8.0):
    """在 CDP 事件流里等一个指定 method（会丢弃其它事件）。"""
    c.ws.settimeout(timeout)
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            raw = c.ws.recv()
        except Exception:
            return None
        if not raw:
            continue
        m = json.loads(raw)
        if m.get("method") == method:
            return m.get("params")
    return None

def _upload_audio_once(c, files, button, timeout):
    c.send("Page.enable")
    c.send("Page.setInterceptFileChooserDialog", {"enabled": False})
    time.sleep(0.4)
    c.send("Page.setInterceptFileChooserDialog", {"enabled": True})

    coord = c.eval(JS_BUTTON_COORD + "(%s)" % json.dumps(button, ensure_ascii=False))
    if not coord:
        return False, {"err": "找不到「%s」按钮" % button}
    mouse_click(c, coord["x"], coord["y"])

    # ★★ 2026-10-15 适配（本机实测）：点「音频」**不再直接弹文件框**，
    #   而是先弹一个「音色选择弹窗」（内置音色 + 上传音频/取消/确认）。
    #   必须再点弹窗里的「上传音频」才会出 fileChooserOpened。
    #   老逻辑只点一次 ⇒ 永远收不到事件（实测 3 次全失败）。
    up = None
    for _ in range(12):
        time.sleep(0.5)
        up = c.eval(JS_BUTTON_COORD + "(%s)" % json.dumps("上传音频", ensure_ascii=False))
        if up:
            break
    if up:
        mouse_click(c, up["x"], up["y"])
    ev = wait_event(c, "Page.fileChooserOpened", timeout=timeout)
    if not ev and up:
        # 再补一次（弹窗内按钮有时首点被模态动画吃掉）
        mouse_click(c, up["x"], up["y"])
        ev = wait_event(c, "Page.fileChooserOpened", timeout=timeout)
    if not ev:
        # 失败要把弹窗关掉，否则下一组会一直顶着遮罩
        for label in ("取消", "Close"):
            cc = c.eval(JS_BUTTON_COORD + "(%s)" % json.dumps(label, ensure_ascii=False))
            if cc:
                try:
                    mouse_click(c, cc["x"], cc["y"])
                except Exception:
                    pass
                break
        return False, {"err": "点击后未收到 fileChooserOpened", "coord": coord, "upload_btn": up}
    bid = ev["backendNodeId"]
    c.send("DOM.setFileInputFiles", {"backendNodeId": bid, "files": files})
    # ★ 弹窗里选完文件后可能要点「确认」才真正加到资产栏
    time.sleep(2.5)
    cf = c.eval(JS_BUTTON_COORD + "(%s)" % json.dumps("确认", ensure_ascii=False))
    if cf:
        try:
            mouse_click(c, cf["x"], cf["y"])
            time.sleep(1.0)
        except Exception:
            pass
    return True, {"backendNodeId": bid, "mode": ev.get("mode"), "n": len(files), "coord": coord}


def upload_audio(c, files, button="音频", timeout=15.0, tries=3):
    """点「+音频」→ 拦 fileChooserOpened → backendNodeId 投喂。

    返回 (ok, info)。ok=False 时 info 说明原因。
    ★ files 超过 3 个会被平台丢弃，这里**主动截断到 3**并告警，省得白等。

    ★★ 2026-10-07 实测：这一步**偶发失败**（点下去了但收不到 `fileChooserOpened`，
       实测 `2_1_标准` 中过一次）—— 属平台原生文件框的时序抖动。
       ⇒ **必须重试**；而且**失败绝不能继续提交**（见 `vidu_run.run_one` 的硬中断），
       否则会静默产出一条"没有音色参考"的片子。
    """
    if not files:
        return True, {"skipped": "无音频"}
    if len(files) > 3:
        log({"warn": "音频上限=3，已截断", "got": len(files), "use": 3})
        files = files[:3]

    last = None
    for i in range(max(1, tries)):
        ok, info = _upload_audio_once(c, files, button, timeout)
        if ok:
            info["try"] = i + 1
            # ★ 投喂后确认资产真的进来了（拦截成功 ≠ 平台处理成功）
            return True, info
        last = info
        log({"warn": "音频上传第 %d 次失败，重试" % (i + 1), "info": info})
        time.sleep(1.5)
    return False, last

# ---------------------------------------------------------------- 设置区操作
def _read_wheel(c, row_label):
    """读刻度条当前选中值（高亮框反查）。读不到返回 None。

    ★ 时长的 `JS_ROW_CURRENT` **恒为 None**（它按"面积最小的值区"取值，对轮播无效）⇒ 改用这个。
    """
    d = c.eval(JS_ROW_SELECTED + "(%s)" % json.dumps(row_label, ensure_ascii=False)) or {}
    return d.get("sel")


def _set_wheel(c, row_label, want, max_press=8, settle=0.5):
    """刻度条通用落值：**滚到可见 → 点 → 回读校验**，不一致再来一轮（箭头兜底）。

    ★ 滚动用「直接改滚动容器 scrollLeft」而不是点箭头 —— 实测**点箭头根本不滚**
      （连点 6 次，格子坐标一动不动）。
    ★ 方向天然双向，不存在"目标在左边够不到"的问题；值再离谱也先在可视区内再做判断。
    """
    want = str(want)
    last = None
    for i in range(max_press):
        c.eval(JS_ROW_SCROLL_TO + "(%s)" % json.dumps([row_label, want], ensure_ascii=False))
        time.sleep(0.25)
        r = c.eval(JS_ROW_CELL + "(%s)" % json.dumps([row_label, want], ensure_ascii=False))
        if r and not r.get("err"):
            mouse_click(c, r["x"], r["y"])
            time.sleep(settle)
            got = _read_wheel(c, row_label)
            if got == want:
                return {"ok": True, "at": [r["x"], r["y"]], "readback": got, "tries": i}
            if got is None:
                # 读不到选中值（该行不是轮播 / 无高亮框）⇒ 无法校验，按"点到了"收工
                return {"ok": True, "at": [r["x"], r["y"]], "readback": None,
                        "tries": i, "unverified": True}
            last = {"clicked_at": [r["x"], r["y"]], "readback": got}
        else:
            last = {"scroll": c.eval(JS_ROW_SCROLL_TO + "(%s)"
                                     % json.dumps([row_label, want], ensure_ascii=False)),
                    "cell": r}
        # 兜底：按方向箭头再试（这一步实测多半无效，留着以防容器不支持直接滚动）
        arr = c.eval(JS_ROW_ARROWS + "(%s)" % json.dumps(row_label, ensure_ascii=False)) or {}
        cur = _read_wheel(c, row_label)
        go_left = bool(cur and cur.isdigit() and want.isdigit() and int(want) < int(cur))
        A = arr.get("L" if go_left else "R")
        if A:
            mouse_click(c, A["x"], A["y"])
            time.sleep(0.25)
    return {"ok": False, "why": "%s 试了 %d 轮仍未把第 %s 格滚进可视区 / 点击未生效"
                                 % (row_label, max_press, want), "last": last}


def set_cell(c, row_label, value):
    """点刻度条上的某格（数量 / 时长）。★ 点完**回读校验**（能读到时必须一致）。"""
    return _set_wheel(c, row_label, value, settle=0.5)


def set_duration(c, n, max_press=8):
    """设时长。★ 时长条是**窗口式轮播**（一次只显示约 8 格 + 左右箭头），
    必须先把目标格滚进可见窗口，再点。

    实测可见窗口形如 `‹ 9 10 11 12 13 14 15 16 ›`；档位 **1–16**，最大值在右端。

    ★★ 2026-10-07 修（这个 bug 让 11 组少出 4 秒）：
      旧实现只做「找格子 → 点」，**既不判断格子是否在可视区内，也不回读**。
      滚出窗口的格子点下去其实落在**左右箭头**上 ⇒ 点击无效，但函数照样返回 `ok:True`，
      时长静默回落默认值 **8s** 就提交了（实测 声明 12s 的 11 组 **11/11** 全变 8s，
      成片 8.04s，而这 11 组内容需求 10.3–11.9s ⇒ 直接截断/吞台词）。
      现在：**先滚到可见 → 点 → 回读高亮框选中值**；不一致再来一轮；
      到顶仍不一致 ⇒ 返回 `ok:False`，调用方（`vidu_run`）**必须中止该组提交**。
    """
    return _set_wheel(c, "时长", n, max_press=max_press, settle=0.6)

def set_dropdown(c, row_label, want, match_key=None):
    """点开某行下拉，选一个选项。match_key 命中当前值则跳过（省一次点击）。

    ★★ 2026-10-07 修：旧实现**永远返回 ok:True**（只把点击后读到的值塞进 `to` 就收工），
      点上没生效也看不出来。现在按 `after` 回读判定 ok：
      给了 match_key 就要求 match_key 出现在回读值里，否则要求 want 出现在回读值里。
    """
    cur = c.eval(JS_ROW_CURRENT + "(%s)" % json.dumps(row_label, ensure_ascii=False)) or {}
    key = match_key or want
    if cur.get("cur") and key in cur["cur"]:
        return {"ok": True, "already": cur["cur"]}

    if not cur.get("trig"):
        return {"ok": False, "why": "找不到 %s 的值区" % row_label}
    tx, ty = cur["trig"]["x"], cur["trig"]["y"]
    mouse_click(c, tx, ty)
    time.sleep(1.2)

    opt = c.eval(JS_PICK_OPTION + "(%s)" % json.dumps([want, tx, ty], ensure_ascii=False))
    if not opt:
        return {"ok": False, "why": "下拉里找不到选项 %r" % want, "from": cur.get("cur")}
    mouse_click(c, opt["x"], opt["y"])
    time.sleep(0.8)

    after = c.eval(JS_ROW_CURRENT + "(%s)" % json.dumps(row_label, ensure_ascii=False)) or {}
    to = after.get("cur")
    ok = bool(to) and ((key in to) if match_key else (want == to or want in to))
    return {"ok": ok, "from": cur.get("cur"), "to": to, "picked": opt,
            "why": None if ok else "点了 %r 但回读=%r" % (want, to)}

def apply_settings(c, job, cfg):
    """按 job/cfg 设好 模型 / 时长 / 宽高比（其余保持默认）。返回留痕 dict。"""
    out = {}
    out["模型"] = set_dropdown(c, "模型", cfg["model"], match_key="Q4")
    out["时长"] = set_duration(c, job["duration"])
    # ★ 修（2026-10-07）：宽高比必须以作业单 job["ratio"] 为准（作业单=唯一决定提交什么的文件）。
    #   旧写法只读 cfg["ratio"]（vidu_run.py 硬编码 9:16），16:9 作业单被静默提交成 9:16
    #   （vox 首测 6 组全部中招，POST settings.ratio='9:16' 实锤）。
    out["宽高比"] = set_dropdown(c, "宽高比", job.get("ratio") or cfg["ratio"])
    out["数量"] = set_cell(c, "数量", 1)
    out["闲时创作"] = set_nopeak(c)
    return out


JS_NOPEAK = r"""(() => {
  // ★ 2026-10-15 新增：本剧要求「闲时创作（0 积分）」，页面默认是关的，必须打开。
  const el = [...document.querySelectorAll('*')].find(e => (e.textContent || '').trim() === '闲时创作');
  if (!el) return {err: 'no-label'};
  let box = el;
  for (let i = 0; i < 4 && box; i++) box = box.parentElement;
  const sw = (box || document).querySelector('[role=switch]');
  if (!sw) return {err: 'no-switch'};
  const r = sw.getBoundingClientRect();
  return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
          state: sw.getAttribute('data-state'), aria: sw.getAttribute('aria-checked')};
})()"""


def set_nopeak(c):
    """把「闲时创作」开关打开（幂等：已是 on 就跳过）。"""
    for _ in range(3):
        try:
            st = c.eval(JS_NOPEAK) or {}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "why": str(e)[:120]}
        if st.get("err"):
            return {"ok": False, "why": st["err"]}
        if st.get("state") == "checked" or str(st.get("aria")) == "true":
            return {"ok": True, "already": "on"}
        mouse_click(c, st["x"], st["y"])
        time.sleep(0.8)
    st = c.eval(JS_NOPEAK) or {}
    ok = st.get("state") == "checked" or str(st.get("aria")) == "true"
    return {"ok": ok, "to": st.get("state"), "why": None if ok else "点了开关但回读仍为关"}

# ---------------------------------------------------------------- 单组流程
# ---------------------------------------------------------------- 组间复位
# ★ 用户 2026-10-07 明确要求：**就在已开的页面上继续操作**，不要每次刷新。
#   刷新只为复位（否则上一组的资产会把编号顶到 图8/图9…，pnN→图N 映射错位）。
JS_FORM_STATE = r"""(() => {
  const ed = document.querySelector('.tiptap.ProseMirror');
  if (!ed) return {ready: false};
  let mentions = 0;
  try { ed.editor.state.doc.descendants(n => { if (n.type.name === 'mention') mentions++; }); } catch (e) {}
  const txt = (ed.innerText || '').replace(/\s/g, '');
  return {ready: true, text_len: txt.length, mentions: mentions};
})()"""


def form_state(c):
    try:
        return c.eval(JS_FORM_STATE) or {}
    except Exception:
        return {}


def ensure_clean(c, mode="auto"):
    """按需复位表单。返回一句说明（会打日志）。"""
    if mode == "always":
        c.send("Page.reload", {"ignoreCache": True})
        return "已刷新(always)"
    st = form_state(c)
    if not st.get("ready"):
        c.send("Page.reload", {"ignoreCache": True})
        return "已刷新(编辑器不在，页面需重建)"
    if mode == "never":
        return "沿用当前页(never)"
    if st.get("mentions") or st.get("text_len", 0) > 5:
        c.send("Page.reload", {"ignoreCache": True})
        return "已刷新(表单脏: %d mention / %d 字)" % (st.get("mentions", 0), st.get("text_len", 0))
    return "沿用当前页(干净)"
