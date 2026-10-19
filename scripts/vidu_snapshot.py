# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:b392b03898 · 溯源标记，请保留
"""
vidu_probe.py —— 一次性抓取 Vidu character2video 页面的可交互元素清单。

用途：给「元素定位」建立权威快照。产出 JSON + 控制台摘要。
不做任何点击 / 上传（只读）。

用法：
  python vidu_probe.py --port 9222 --out <路径.json>
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vidu_cdp import CDP  # noqa: E402

# 给每个元素生成一条尽量稳定的 CSS 路径（带 nth-of-type），便于后续定位/排查
JS_HELPERS = r"""
window.__vdPath = function(el){
  const parts = [];
  while (el && el.nodeType === 1 && parts.length < 8) {
    let s = el.tagName.toLowerCase();
    if (el.id) { s += '#' + el.id; parts.unshift(s); break; }
    const cls = (typeof el.className === 'string' ? el.className : '').trim().split(/\s+/).filter(Boolean).slice(0,2);
    if (cls.length) s += '.' + cls.join('.');
    const p = el.parentElement;
    if (p) {
      const sib = Array.from(p.children).filter(x => x.tagName === el.tagName);
      if (sib.length > 1) s += ':nth-of-type(' + (sib.indexOf(el)+1) + ')';
    }
    parts.unshift(s);
    el = el.parentElement;
  }
  return parts.join(' > ');
};
window.__vdVisible = function(el){
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return !!(r.width > 1 && r.height > 1 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0');
};
window.__vdRect = function(el){ const r = el.getBoundingClientRect(); return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]; };
"""

JS_FILES = r"""
(() => {
  __vdPath; __vdVisible; __vdRect;
  const out = [];
  document.querySelectorAll('input[type=file]').forEach((el, i) => {
    out.push({
      i, tag: el.tagName.toLowerCase(), type: 'file',
      accept: el.getAttribute('accept'), multiple: !!el.multiple,
      name: el.getAttribute('name'), id: el.id,
      visible: __vdVisible(el), rect: __vdRect(el), path: __vdPath(el),
      parentText: (el.closest('div') ? (el.closest('div').innerText || '') : '').replace(/\s+/g,' ').trim().slice(0, 120),
    });
  });
  return out;
})()
"""

JS_CLICKABLE = r"""
(() => {
  __vdPath; __vdVisible; __vdRect;
  const seen = new Set(), out = [];
  const add = (el, kind) => {
    if (seen.has(el)) return; seen.add(el);
    const r = el.getBoundingClientRect();
    out.push({
      kind, tag: el.tagName.toLowerCase(),
      text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 50),
      aria: el.getAttribute('aria-label'), title: el.getAttribute('title'),
      role: el.getAttribute('role'),
      cls: (typeof el.className === 'string' ? el.className : '').slice(0, 160),
      visible: __vdVisible(el), rect: __vdRect(el), path: __vdPath(el),
    });
  };
  document.querySelectorAll('button, [role=button], [class*=btn], [class*=Btn]').forEach(el => add(el, 'button'));
  document.querySelectorAll('a[href]').forEach(el => add(el, 'link'));
  return out.filter(x => x.visible);
})()
"""

JS_INPUTS = r"""
(() => {
  __vdPath; __vdVisible; __vdRect;
  const out = [];
  document.querySelectorAll('textarea, input:not([type=file]), [contenteditable=true], [contenteditable=""], [contenteditable=plaintext-only]').forEach(el => {
    out.push({
      tag: el.tagName.toLowerCase(), type: el.getAttribute('type'),
      placeholder: el.getAttribute('placeholder'),
      cls: (typeof el.className === 'string' ? el.className : '').slice(0, 160),
      contenteditable: el.getAttribute('contenteditable'),
      valueLen: (el.value || el.innerText || '').length,
      visible: __vdVisible(el), rect: __vdRect(el), path: __vdPath(el),
      parentText: (el.parentElement ? (el.parentElement.innerText || '') : '').replace(/\s+/g,' ').trim().slice(0, 100),
    });
  });
  return out;
})()
"""

JS_TEXTMAP = r"""
(() => {
  const out = [];
  const walk = (el, depth) => {
    if (depth > 7) return;
    for (const c of el.children) {
      const t = (c.childElementCount === 0) ? (c.innerText || '').replace(/\s+/g,' ').trim() : '';
      if (t) { const r = c.getBoundingClientRect(); if (r.width>1&&r.height>1) out.push({d: depth, t: t.slice(0, 40), y: Math.round(r.y), x: Math.round(r.x)}); }
      walk(c, depth + 1);
    }
  };
  walk(document.body, 0);
  return out.slice(0, 400);
})()
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--url-contains", default="vidu.cn")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    with CDP(port=a.port, url_contains=a.url_contains) as c:
        c.eval("1")  # 握手
        c.eval(JS_HELPERS + ";1")
        title = c.eval("document.title")
        url = c.eval("location.href")
        files = c.eval(JS_FILES)
        clickables = c.eval(JS_CLICKABLE)
        inputs = c.eval(JS_INPUTS)
        textmap = c.eval(JS_TEXTMAP)

    snap = {"title": title, "url": url, "file_inputs": files,
            "clickables": clickables, "inputs": inputs, "textmap": textmap}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)

    print("URL:", url)
    print("TITLE:", title)
    print("\n== input[type=file] (%d) ==" % len(files))
    for f_ in files:
        print("  [%d] accept=%r multiple=%s vis=%s rect=%s" % (f_["i"], f_["accept"], f_["multiple"], f_["visible"], f_["rect"]))
        print("      ctx:", f_["parentText"][:80])
    print("\n== 可见按钮 (%d) ==" % len(clickables))
    for b in clickables:
        if b["kind"] == "button":
            print("  %-38r rect=%s cls=%.50s" % (b["text"], b["rect"], b["cls"]))
    print("\n== 输入区 (%d) ==")
    for i in inputs:
        print("  %-9s ph=%r ce=%r len=%d vis=%s rect=%s cls=%.45s" % (i["tag"], i.get("placeholder"), i.get("contenteditable"), i["valueLen"], i["visible"], i["rect"], i["cls"]))
    print("\n已写入:", a.out)


if __name__ == "__main__":
    main()
