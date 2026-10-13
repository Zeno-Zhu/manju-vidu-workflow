# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:7a662874ab · 溯源标记，请保留
"""
vidu_verify.py —— **只读**核验当前 Vidu 创作页上的表单状态（不刷新、不点击、不提交）。

用途：`vidu_run.py --mode upload-only` 跑完后，用它「看」页面真实内容：
  · 编辑器文档逐段导出（mention 以 ⟦图N⟧/⟦音频N⟧ 占位），便于确认 mention 位置对不对
  · 残留检查：正文里不该再出现 `=@`（说明 mention 没被正确替换）
  · 资产栏缩略图数、设置区当前值（模型/时长/宽高比/数量）
  · 整页截图（含资产栏），落盘供人工目检

用法：
  python vidu_verify.py --port 9222 [--shot <png>] [--tail 20] [--no-shot]

★ 绝不调用 Page.reload / DOM.setFileInputFiles —— 只读。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vidu_cdp import CDP  # noqa: E402

JS_DOC = r"""(() => {
  const el = document.querySelector('.tiptap.ProseMirror');
  if (!el) return {err: 'no-editor'};
  const ed = el.editor;
  if (!ed) return {err: 'no-editor-view'};
  const paras = [];
  ed.state.doc.forEach(node => {
    if (node.type.name !== 'paragraph') return;
    let s = '';
    node.forEach(ch => {
      if (ch.type.name === 'mention') s += '⟦' + (ch.attrs.label || '?') + '⟧';
      else s += ch.text;
    });
    paras.push(s);
  });
  return {ok: true, paras: paras, n: paras.length};
})()"""

JS_ASSETS = r"""(() => {
  // 资产栏缩略图：找 img[src^=blob|http] 里宽高在 40~200 的，作为"已上传资产"的粗估
  const out = {thumbs: 0, labels: []};
  document.querySelectorAll('img').forEach(im => {
    const r = im.getBoundingClientRect();
    if (r.width >= 36 && r.width <= 220 && r.height >= 36 && r.height <= 220) out.thumbs++;
  });
  return out;
})()"""

JS_SETTINGS = r"""(() => {
  const lock = (document.body.innerText || '').replace(/\s+/g, '');
  const grab = (a, b) => { const i = lock.indexOf(a); if (i < 0) return null;
    const j = lock.indexOf(b, i + a.length); return j < 0 ? null : lock.slice(i + a.length, j); };
  return {
    模型: grab('模型', '时长'),
    时长: grab('时长', '清晰度'),
    清晰度: grab('清晰度', '宽高比'),
    宽高比: grab('宽高比', '数量'),
    数量: grab('数量', '创作'),
    btn创作: /创作/.test(lock),
  };
})()"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--shot", default=r"C:\Users\Administrator\.manju-vidu\shots\_verify_full.png")
    ap.add_argument("--no-shot", action="store_true")
    ap.add_argument("--tail", type=int, default=0, help=">0 时只打印最后 N 段")
    a = ap.parse_args()

    with CDP(port=a.port, url_contains="vidu.cn", timeout=30) as c:
        c.send("Page.bringToFront")
        doc = c.eval(JS_DOC) or {}
        if doc.get("err"):
            print("★ 编辑器不可用:", doc["err"]); return 1
        paras = doc["paras"]
        print("段落数:", doc["n"])

        n_men = sum(p.count("⟦") for p in paras)
        print("mention 总数:", n_men)
        bad = [i for i, p in enumerate(paras, 1) if "=@" in p]
        print("残留 `=@` 的段:", bad if bad else "无 ✅")

        print("设置区:", json.dumps(c.eval(JS_SETTINGS), ensure_ascii=False))
        print("资产缩略图(粗估):", json.dumps(c.eval(JS_ASSETS), ensure_ascii=False))

        show = paras[-a.tail:] if a.tail > 0 else paras
        for i, p in enumerate(show, 1 + (len(paras) - len(show))):
            print("%3d | %s" % (i, p[:160]))

        if not a.no_shot:
            os.makedirs(os.path.dirname(a.shot), exist_ok=True)
            try:
                c.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
            except Exception:
                pass
            c.screenshot(a.shot, full_page=True)
            print("整页截图:", a.shot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
