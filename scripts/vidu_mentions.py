# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:0762992871 · 溯源标记，请保留
"""vidu_mentions.py —— mention 节点机制（把 `pnN,名称=@` / `vmN,名称=@` 放回原位）。

★ 平台在上传资产后**自动**往编辑器插 mention（默认堆在末尾）；
  正解是用 `EditorView` **重建整篇文档**把节点放回声明位置，并**沿用上传时的 id**
  ⇒ 绑定关系不丢。
⛔ 不要搬 DOM 节点（会与 ProseMirror 文档模型脱同步，看着对、提交时丢）。

★ 顺序映射：`pnN ↔ 图N`、`vmN ↔ 音频N`（2026-10-07 probe 实测）。
"""
from __future__ import annotations

import json
import re
import time

from vidu_js import JS_COLLECT_MENTIONS, JS_SET_BLOCKS
from vidu_log import log

# ---------------------------------------------------------------- mention 机制
# ★★ 2026-10-07 探明（这是本渠道最关键的技术点，别再用"搬 DOM"的笨办法）：
#   · tiptap 实例直接挂在 DOM 上：`document.querySelector('.tiptap.ProseMirror').editor.view`
#     ⇒ 一次就能拿到 ProseMirror 的 `EditorView`（不需要翻 React fiber）。
#   · schema 里**真的有 `mention` 节点**：nodes = ['paragraph','mention','doc','text']。
#   · 上传资产后平台**自动往编辑器里插 mention 节点**，其模型层结构为：
#       {type:'mention', attrs:{id, label, color, preview, mediaType}}
#     其中 `label` 是平台命名（图片=图1/图2…，按上传顺序），`id` 是绑定用的 UUID。
#   · ⇒ **不需要移动 DOM 节点**（那样会和 ProseMirror 的文档模型脱同步，
#     看着对、提交时丢）。正确做法是：**拿 EditorView 重建整篇文档**，
#     把 mention 节点放在任意位置，并沿用上传时生成的 id ⇒ 绑定关系不丢。
#
# 图片命名与「云鲸 pnN」是**同序**的：pn1=第一张上传=图1，pn2=图2…（已核对 1_1 全组）

def collect_mentions(c):
    """上传完资产后，把平台自动插入的 mention 节点读出来：{label: attrs}。"""
    r = c.eval(JS_COLLECT_MENTIONS) or {}
    if r.get("err"):
        log({"warn": "collect_mentions", "err": r["err"]})
        return {}
    return r.get("mentions") or {}

def build_blocks(prompt: str, images, audios, mention_labels):
    """把提示词切成 blocks（每块=一段），并在 `名称=@` 处放**真 mention 节点**。

    保留用户要的可读写法：
      · `pn1,二丫少女=@`                        → 文本 `pn1,二丫少女=` + mention(图1)
      · `vm1,年轻女性｜自然01（=参考音频1，用于二丫）=@` → 文本 `vm1,…（=参考音频1，用于二丫）=` + mention(音频1)
    映射依据（2026-10-07 probe 实测，1_1 全组核对）：
      · pn<N> ↔ 第 N 张上传图 ↔ label `图N`
      · vm<N> ↔ 第 N 个上传音 ↔ label `音频N`
    ★★ 旧版只认 pn ⇒ 音频 mention 会被当成"没引用的资产"堆到正文末尾，
       而且 `vmN,…=@` 整行会**原样残留在正文里**（含 `=@`）。本版已修：两类都放回原位。

    返回 (blocks, used_labels, rest_labels)：
      · used = 被正文引用掉的 mention label（正常=全部）
      · rest = 上传了但正文没引用的 → 附在末尾一段，避免"资产没绑上"
    """
    img_label = {i + 1: "图%d" % (i + 1) for i in range(len(images))}
    aud_label = {i + 1: "音频%d" % (i + 1) for i in range(len(audios))}
    # `=@` 是唯一可靠终止符：名字里可能含 `（=参考音频1，用于二丫）` 这种带 `=`/`，` 的片段，
    # 所以用惰性 `.*?` 匹配到**最早的 `=@`**（内层 `=` 后面跟的是汉字，不会误命中）。
    RE_M = re.compile(r"(pn|vm)(\d+),(.*?)=\s*@")
    blocks, used, unmatched = [], set(), []
    for raw in prompt.split("\n"):
        parts, buf = [], raw
        while True:
            m = RE_M.search(buf)
            if not m:
                if buf:
                    parts.append({"t": buf})
                break
            if m.start():
                parts.append({"t": buf[:m.start()]})
            # 原样保留「pn1,名称=」/「vm1,名称（…）=」，只把结尾的 `@` 换成真 mention 节点
            parts.append({"t": "%s%s=" % (m.group(1) + m.group(2), "," + m.group(3))})
            kind, n = m.group(1), int(m.group(2))
            lab = (img_label if kind == "pn" else aud_label).get(n)
            if lab and lab in mention_labels:
                parts.append({"m": lab})
                used.add(lab)
            else:
                parts.append({"t": "@"})       # 资产没拿到 → 留可见记号，便于肉眼发现
                unmatched.append((kind + str(n), lab))
            buf = buf[m.end():]
        blocks.append(parts)

    # 正文没引用的 mention（正常应为空）→ 末尾单独一段，保证资产全部绑定
    rest = [k for k in mention_labels if k not in used]
    if rest:
        tail = []
        for k in rest:
            tail.append({"t": "%s = " % k})
            tail.append({"m": k})
            tail.append({"t": "  "})
        blocks.append(tail)
    if unmatched:
        log({"warn": "build_blocks 未绑上的资产", "items": unmatched})
    return blocks, used, rest

def wait_mentions(c, n_expect, timeout=120):
    """等平台把上传的资产自动插成 mention 节点（上传是异步的，插节点有延迟）。

    返回 (mentions, ok)。ok=False 表示超时（label 数没到预期）—— caller 自己决定要不要继续。
    """
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout:
        try:
            c.send("Emulation.setFocusEmulationEnabled", {"enabled": True})  # 防等待期间标签被切到后台而停渲染
        except Exception:
            pass
        last = collect_mentions(c)
        if len(last) >= n_expect:
            return last, True
        time.sleep(2)
    return last, False
