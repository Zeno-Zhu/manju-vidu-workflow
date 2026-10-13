# -*- coding: utf-8 -*-
"""vidu_js.py —— 在 Vidu 页面上执行的**全部 JS 片段**（纯字符串常量，无业务逻辑）。

为什么单独放：这些片段里带着大量**实测注释**（DOM 结构、作用域收窄的坑、
为什么不能整页 querySelectorAll），塞在业务模块里会把文件撑到几百行。

导出清单：
  · 上传/点击   JS_FILE_INPUTS / JS_CLICK_TEXT / JS_BUTTON_COORD / JS_PICK_INPUT
  · 设置区      _PRELUDE / wrap / JS_ROW_CURRENT / JS_ROW_CELL / JS_ROW_SELECTED / JS_ROW_ARROWS / JS_PICK_OPTION
  · 编辑器      JS_EDITOR_TEXT / JS_EDITOR_CLEAR
  · mention     JS_COLLECT_MENTIONS / JS_SET_BLOCKS
  · 提交        JS_SUBMIT

★ 改这些片段前先读 `references/vidu-平台机制.md` §3/§4（定位四坑）。
"""
from __future__ import annotations

JS_COLLECT_MENTIONS = r"""(() => {
  const el = document.querySelector('.tiptap.ProseMirror');
  const ed = el && el.editor;
  if (!ed) return {err: 'no-editor'};
  const out = {};
  ed.state.doc.descendants(n => {
    if (n.type.name === 'mention') out[n.attrs.label] = n.attrs;
  });
  return {ok: true, mentions: out, text: el.innerText};
})()"""

JS_SET_BLOCKS = r"""((spec) => {
  const el = document.querySelector('.tiptap.ProseMirror');
  const ed = el && el.editor;
  if (!ed) return 'no-editor';
  const attrs = spec.attrs || {};
  const content = spec.blocks.map(b => {
    const parts = [];
    for (const p of b) {
      if (p.t !== undefined) {
        const t = String(p.t);
        if (t) parts.push({type: 'text', text: t});
      } else {
        const a = attrs[p.m];
        if (a) parts.push({type: 'mention', attrs: a});
        else parts.push({type: 'text', text: '@' + p.m});   // 找不到就退化成文本，便于肉眼发现
      }
    }
    return parts.length ? {type: 'paragraph', content: parts} : {type: 'paragraph'};
  });
  ed.commands.setContent({type: 'doc', content: content}, true);
  return 'OK:' + JSON.stringify({nBlocks: content.length});
})"""

JS_FILE_INPUTS = r"""(() => Array.from(document.querySelectorAll('input[type=file]'))
  .map((el, i) => ({i, accept: (el.accept||'').toLowerCase(), multiple: !!el.multiple})))()"""

JS_CLICK_TEXT = r"""((want) => {
  let hit = null;
  document.querySelectorAll('button,a,div,span,li').forEach(el => {
    if (hit) return;
    if ((el.innerText || '').trim() !== want) return;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return;
    hit = el;
  });
  if (!hit) return 'NOT_FOUND';
  hit.scrollIntoView({block: 'center'});
  hit.click();
  return 'OK';
})"""

# ═══ 设置区（2026-10-07 实测结构，别乱猜）═══
#   · **模型**       = 下拉（默认已是「Vidu Q4 预览版」，通常无需动）
#   · **时长**       = 横向刻度条，16 格、每格 27px、单击直接生效（不是下拉！）
#   · **清晰度 & 编码格式** = 下拉（默认 H265）
#   · **宽高比**     = 下拉，选项向上弹出：16:9 / 9:16 / 1:1 / 4:3 / 3:4
#   · **数量**       = 分段 1/2/3/4（默认 1）
#   ⚠ 所有行标签（时长/宽高比/数量/模型）都在 x≈137，值区在 x≈337–593。
#     点「行标签 + 220px」会落在值区 —— 但**时长/数量是刻度条，那样点等于直接选值**，
#     所以这两行必须按"格子文本"定位，不能用偏移量。

# ★★ 作用域收窄（2026-10-07 踩过）：整页 querySelectorAll 会**误命中右侧「参考生视频」历史卡片**
#   —— 卡片文案里也有「Vidu Q4 预览版」「图1」「时长: 16s」这类文本，结果把下拉选项点到了右栏
#   （实测 `模型 -> from:"图1" picked:{x:1341,y:153}`）。
#   对策：所有查询先缩到**左侧创作面板**（＝从编辑器往上找第一个含「宽高比」的祖先），
#         并且候选必须落在行标签右侧 400px 内；找不到再回退全文档。
# ⚠ 必须包进 IIFE：`Runtime.evaluate` 的顶层 `const` 会留在该执行上下文里，
#   第二次求值就报 `SyntaxError: Identifier '__root' has already been declared`（实测踩到）。
_PRELUDE = r"""
  const __root = (() => {
    const ed = document.querySelector('.tiptap.ProseMirror');
    if (!ed) return null;
    let r = ed;
    while (r.parentElement && !((r.textContent || '').includes('宽高比'))) r = r.parentElement;
    return r;
  })();
  const __scopes = (__root && __root !== document.body) ? [__root, document] : [document];
  // ★★ 必须排除编辑器内的节点：提示词框是**内部可滚动容器**，里面的 mention 徽标（图1/图2…）
  //   虽然被滚动条裁掉、肉眼看不见，但 `getBoundingClientRect()` 仍返回**未裁剪的布局坐标**，
  //   于是它"几何上"落进了设置行的 y 带，还因为面积小把真正的值（Vidu Q4 预览版）挤掉了。
  //   ⇒ 实测 `模型 -> from:"图1"`，把它点到了右栏。所有设置区查询一律排除 .tiptap.ProseMirror 后代。
  const __inEditor = (el) => !!(el.closest && el.closest('.tiptap.ProseMirror'));
  const __findLabel = (rowLabel) => {
    for (const sc of __scopes) {
      let hit = null;
      sc.querySelectorAll('div,span,p,label').forEach(el => {
        if (hit) return;
        if ((el.innerText || '').trim() !== rowLabel) return;
        const r = el.getBoundingClientRect();
        if (r.width > 6 && r.height > 6 && r.width < 140) hit = el;
      });
      if (hit) return hit;
    }
    return null;
  };
  // ★★ 2026-10-07：刻度条（时长 / 数量）是**横向可滚动轮播**，一次只显示约 8 格。
  //   被滚出可视区的格子 `getBoundingClientRect()` **仍返回未裁剪的布局坐标**，
  //   点在那个坐标上实际落到**左/右箭头**上 ⇒ 点击静默无效（不报错）。
  //   ⇒ 必须先定位滚动容器，再判断格子是否真的在可视区内（见 JS_ROW_CELL）。
  const __scrollStrip = (el) => {
    let n = el && el.parentElement;
    while (n && n !== document.body) {
      const cs = getComputedStyle(n);
      if ((cs.overflowX === 'auto' || cs.overflowX === 'scroll') &&
          n.getBoundingClientRect().width > 60) return n;
      n = n.parentElement;
    }
    return null;
  };
"""

def wrap(fn_src):
    """把函数字面量包进带 prelude 的 IIFE，返回一个「可再跟 (args) 调用」的表达式。"""
    return "(() => {\n%s\n  return (%s);\n})()" % (_PRELUDE, fn_src)

JS_ROW_CURRENT = wrap(r"""((rowLabel) => {
  const labEl = __findLabel(rowLabel);
  if (!labEl) return {err: 'no-label'};
  labEl.scrollIntoView({block: 'center'});
  const lab = labEl.getBoundingClientRect();
  const cy = lab.y + lab.height / 2;
  const cands = [];
  document.querySelectorAll('div,span').forEach(el => {
    if (el.children.length) return;
    if (__inEditor(el)) return;                 // ★ 见 prelude 注释
    const t = (el.textContent || '').trim();
    if (!t) return;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8 || r.width > 320) return;
    if (Math.abs((r.y + r.height / 2) - cy) > 20) return;
    const off = r.x - (lab.x + lab.width);
    if (off < -4 || off > 400) return;          // ★ 只认标签右侧 400px 内的值区
    cands.push({t, x: r.x, y: r.y, w: r.width, h: r.height});
  });
  cands.sort((a, b) => a.w * a.h - b.w * b.h);
  return {cur: cands.length ? cands[0].t : null,
          trig: cands.length ? {x: Math.round(cands[0].x + cands[0].w / 2),
                                y: Math.round(cands[0].y + cands[0].h / 2)} : null};
})""")

# ★★ 2026-10-07 新增：把刻度条**滚到目标格可见**（确定性做法）。
#   为什么不用左右箭头：实测点箭头（Input.dispatchMouseEvent）**不会滚动刻度条** ——
#   连点 6 次后所有格子的 getBoundingClientRect 一动不动。直接改滚动容器的 scrollLeft
#   才是可靠手段：把目标格的中心对齐到可视区中心即可（多滚/少滚都由后续回读兜底）。
JS_ROW_SCROLL_TO = wrap(r"""((spec) => {
  const [rowLabel, want] = spec;
  const labEl = __findLabel(rowLabel);
  if (!labEl) return {err: 'no-label'};
  labEl.scrollIntoView({block: 'center'});
  const lab = labEl.getBoundingClientRect();
  const cy = lab.y + lab.height / 2;
  let cell = null;
  document.querySelectorAll('div,span,label').forEach(el => {
    if (__inEditor(el)) return;
    if ((el.textContent || '').trim() !== String(want)) return;
    const r = el.getBoundingClientRect();
    if (r.width < 12 || r.height < 12 || r.width > 90 || r.height > 90) return;
    if (Math.abs((r.y + r.height / 2) - cy) > 20) return;
    const off = r.x - lab.x;
    if (off < -20 || off > 460) return;
    if (!cell || el.children.length < cell.children.length) cell = el;
  });
  if (!cell) return {err: 'no-cell', want: String(want)};
  cell.scrollIntoView({block: 'nearest', inline: 'center'});
  const strip = __scrollStrip(cell);
  if (!strip) return {err: 'no-strip', at: Math.round(cell.getBoundingClientRect().x)};
  const s = strip.getBoundingClientRect();
  const r = cell.getBoundingClientRect();
  const delta = (r.x + r.width / 2) - (s.x + s.width / 2);
  if (Math.abs(delta) > 1) strip.scrollLeft += delta;
  const s2 = strip.getBoundingClientRect();
  const r2 = cell.getBoundingClientRect();
  return {ok: true, delta: Math.round(delta), scrollLeft: Math.round(strip.scrollLeft),
          vis: (r2.x >= s2.x - 1 && r2.right <= s2.right + 1)};
})""")

JS_ROW_CELL = wrap(r"""((spec) => {
  const [rowLabel, want] = spec;
  const labEl = __findLabel(rowLabel);
  if (!labEl) return {err: 'no-label'};
  labEl.scrollIntoView({block: 'center'});
  const lab = labEl.getBoundingClientRect();
  const cy = lab.y + lab.height / 2;
  const cands = [];
  document.querySelectorAll('div,span,label').forEach(el => {
    if (__inEditor(el)) return;                 // ★ 同上，排除提示词框内的徽标
    if ((el.textContent || '').trim() !== String(want)) return;
    const r = el.getBoundingClientRect();
    if (r.width < 12 || r.height < 12 || r.width > 90 || r.height > 90) return;
    if (Math.abs((r.y + r.height / 2) - cy) > 20) return;
    // ⚠ 刻度条**从左边缘起就与行标签水平重叠**（时长条第 1 格 x=142 < 标签右边缘 165），
    //   所以这里只能相对 lab.x 放宽，不能要求"在标签右侧"；上界 460 用来排除右栏。
    const off = r.x - lab.x;
    if (off < -20 || off > 460) return;
    cands.push({el, r});
  });
  if (!cands.length) return {err: 'no-cell', want: String(want)};
  cands.sort((a, b) => (a.el.children.length - b.el.children.length) || (a.r.x - b.r.x));
  const c0 = cands[0];
  const mid = [Math.round(c0.r.x + c0.r.width / 2), Math.round(c0.r.y + c0.r.height / 2)];
  // ★★ 2026-10-07 修：格子必须**整个落在滚动可视区内**，否则坐标会落到左右箭头上。
  //   加这个判断后，set_duration 的"找不到 → 按右箭头"循环才真正会滚动刻度条；
  //   否则它会返回一个"夹在窗口外"的坐标，点了等于没点（实测 12 档 11/11 因此失效）。
  const strip = __scrollStrip(c0.el);
  if (strip) {
    const s = strip.getBoundingClientRect();
    if (!(c0.r.x >= s.x - 1 && c0.r.right <= s.right + 1)) {
      return {err: 'clipped', want: String(want), at: mid,
              strip: [Math.round(s.x), Math.round(s.right)]};
    }
  }
  return {x: mid[0], y: mid[1]};
})""")

# ★★ 2026-10-07 新增：读刻度条**当前选中值**。
#   为什么不能复用 JS_ROW_CURRENT：它按"标签右侧 400px 内**面积最小**的叶子"取值，
#   对下拉（模型/宽高比）有效，但对轮播刻度条 **恒返回 None**（实测）。
#   这里改用轮播自带的**高亮框**（class 含 absolute+rounded-8+border 的那个 div）位置，
#   反查哪个数字格的中心落在框内 ⇒ 拿到真实选中值，用于点击后的强制回读校验。
JS_ROW_SELECTED = wrap(r"""((rowLabel) => {
  const labEl = __findLabel(rowLabel);
  if (!labEl) return {err: 'no-label'};
  labEl.scrollIntoView({block: 'center'});
  const lab = labEl.getBoundingClientRect();
  const cy = lab.y + lab.height / 2;
  let hi = null;
  document.querySelectorAll('div').forEach(el => {
    if (__inEditor(el)) return;
    const c = (el.className || '').toString();
    if (c.indexOf('absolute') < 0 || c.indexOf('rounded-8') < 0 || c.indexOf('border') < 0) return;
    const r = el.getBoundingClientRect();
    if (r.width < 12 || r.width > 90 || r.height < 12) return;
    if (Math.abs((r.y + r.height / 2) - cy) > 20) return;
    hi = r;
  });
  if (!hi) return {err: 'no-highlight'};
  const hx = hi.x + hi.width / 2;
  let sel = null;
  document.querySelectorAll('label,span,div').forEach(el => {
    if (__inEditor(el)) return;
    const t = (el.textContent || '').trim();
    if (!/^\d+$/.test(t)) return;
    const r = el.getBoundingClientRect();
    if (r.width < 12 || r.width > 90) return;
    if (Math.abs((r.y + r.height / 2) - cy) > 20) return;
    if (Math.abs((r.x + r.width / 2) - hx) < 8) sel = t;
  });
  return {sel};
})""")

JS_ROW_ARROWS = wrap(r"""((rowLabel) => {
  const labEl = __findLabel(rowLabel);
  if (!labEl) return {err: 'no-label'};
  labEl.scrollIntoView({block: 'center'});
  const lab = labEl.getBoundingClientRect();
  const cy = lab.y + lab.height / 2;
  let L = null, R = null;
  document.querySelectorAll('div').forEach(el => {
    const cls = (el.className || '').toString();
    const r = el.getBoundingClientRect();
    if (r.width < 10 || r.width > 44 || r.height < 20) return;
    if (Math.abs((r.y + r.height / 2) - cy) > 26) return;
    if (cls.includes('rounded-l-8')) L = {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
    if (cls.includes('rounded-r-8')) R = {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
  });
  return {L, R};
})""")

JS_PICK_OPTION = r"""((spec) => {
  const [want, nx, ny] = spec;
  const o = [];
  document.querySelectorAll('*').forEach(el => {
    if (el.children.length) return;
    if (el.closest && el.closest('.tiptap.ProseMirror')) return;   // ★ 排除提示词框内的徽标
    if ((el.textContent || '').trim() !== want) return;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 6) return;
    const cx = r.x + r.width / 2, cyy = r.y + r.height / 2;
    if (Math.abs(cx - nx) > 280) return;
    o.push({x: cx, y: cyy, d: Math.abs(cyy - ny)});
  });
  if (!o.length) return null;
  o.sort((a, b) => a.d - b.d);
  return {x: Math.round(o[0].x), y: Math.round(o[0].y)};
})"""

JS_EDITOR_TEXT = r"""(() => { const e = document.querySelector('.tiptap.ProseMirror');
   return e ? e.innerText : null; })()"""

JS_EDITOR_CLEAR = r"""(() => { const e = document.querySelector('.tiptap.ProseMirror');
   if (!e) return 'no-editor';
   e.focus();
   const s = getSelection(), r = document.createRange();
   r.selectNodeContents(e); s.removeAllRanges(); s.addRange(r);
   document.execCommand('delete');
   return e.innerText; })()"""

JS_PICK_INPUT = r"""((want) => {
  const isAud = a => /wav|mp3|aac|flac|ogg|aiff|aif/.test(a || '');
  const els = Array.from(document.querySelectorAll('input[type=file]'));
  const hit = els.find(e => want === 'audio'
        ? isAud(e.accept)
        : (e.accept && !isAud(e.accept)));
  return hit || null;
})"""

JS_SUBMIT = wrap(r"""(() => {
  // ★★ 首选 `data-testid=form-submit-button`（实测存在、唯一、稳定）。
  //    旧的"按文本 startsWith('创作')"会被右栏历史卡片的「继续创作」等干扰。
  const tgt = document.querySelector('[data-testid=form-submit-button]');
  if (!tgt) return {err: 'no-submit-btn'};
  tgt.scrollIntoView({block: 'center'});
  const r = tgt.getBoundingClientRect();
  return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
          text: (tgt.innerText || '').trim().slice(0, 30),
          disabled: !!tgt.disabled,
          ariaDisabled: tgt.getAttribute('aria-disabled')};
})""") + "()"
