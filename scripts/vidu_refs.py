# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:b6e4ba60f0 · 溯源标记，请保留
"""vidu_refs.py —— Vidu「主体库」批量管理（★ 纯 API，不点 UI）。

═══ 它解决什么 ═══
Vidu 主体库（页面 `https://www.vidu.cn/assets/myreferences`）手动一个个建/删很慢。
本模块把整条链路**全部 API 化**（2026-10-13 逐条实测），一行命令批量建 / 批量删。

═══ 实测到的接口（全部 200；base = https://service.vidu.cn）═══
  列表      GET    /vidu/v1/material/elements/personal?pager.pagesz=100  → {elements:[...], next_page_token}
  重名校验  GET    /vidu/v1/material/elements/create/check?name=<urlenc>
  预处理    POST   /vidu/v1/material/elements/pre-process   ← 平台 AI 读图，生成 recaption{style,description}
  创建      POST   /vidu/v1/material/elements
  删除      DELETE /vidu/v1/material/elements/{id}          → {}
  标签列表  GET    /vidu/v1/material/labels                 → {labels:[{id,name,creator_id,created_at}]}
  标签创建  POST   /vidu/v1/material/labels   {"name":…}     → {id,name,…}
  标签删除  DELETE /vidu/v1/material/labels/{id}            → {}
  上传 init POST   /tools/v1/files/uploads  {"metadata":{"image-height","image-width"},"scene":"material"}
                    → {id, put_url, headers, expires_at}
  上传 PUT         <put_url>  （带 init.headers，body=原始字节）
  上传 完成 PUT    /tools/v1/files/uploads/{id}/finish  {"etag":…,"id":…}

★★ 必需请求头（缺了会被 CORS 预检拒掉，页面内 fetch 直接报 `TypeError: Failed to fetch`）：
     X-Platform: web      X-App-Version: -
   ★ 这两个是 2026-10-13 从抓包 header 抄出来的；只带 cookie 不够。

★★ 上传为什么要传两次：官方 `components[]` 里 `content`（裁剪图）与 `src_img`（原图）
   用的是**两个不同的 upload id**（实测同一张图确实发起 2 次 POST /uploads）。
   本模块照抄：每次创建传两份同字节，分别挂 content / src_img。

★ 全部请求在**页面上下文**里发（复用登录态 + 已配好的 CORS），Python 只做编排与传参。

═══ 用法 ═══
  PY="C:/Users/Administrator/AppData/Local/Microsoft/WindowsApps/python.exe"
  S="C:/Users/Administrator/.workbuddy/skills/manju-vidu/scripts"

  $PY $S/vidu_refs.py list                       # 列主体（名字/标签/id）
  $PY $S/vidu_refs.py list --label 雪花膏         # 只列某剧
  $PY $S/vidu_refs.py labels                     # 列标签
  $PY $S/vidu_refs.py create --name 二丫少女 --image "<p>.png" --label 雪花膏
  $PY $S/vidu_refs.py import --manifest refs.json --dry-run
  $PY $S/vidu_refs.py import --manifest refs.json
  $PY $S/vidu_refs.py delete --label 雪花膏 --dry-run    # 先看要删什么（默认就是 dry-run）
  $PY $S/vidu_refs.py delete --label 雪花膏 --yes         # 真删

⚠ 必须用**系统 Python 3.14**（CDP 依赖 websocket-client），且**在沙箱外**跑。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vidu_cdp import CDP            # noqa: E402
from vidu_log import log            # noqa: E402

# ★ 永不删除的「保留标签」——用户常用主体（技能库图等）沉淀用，清理主体库时跳过
KEEP_LABEL_DEFAULT = "常用"
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

# ════════════════════════════════════════════════════════════════════════
# 页面上执行的 JS：全部 API 调用集中在这里（按 spec.op 分支）
# ════════════════════════════════════════════════════════════════════════
JS_CORE = r"""(async (spec) => {
  const S = 'https://service.vidu.cn';
  const MAT = S + '/vidu/v1/material';
  const H = {'X-Platform':'web','X-App-Version':'-','Content-Type':'application/json'};
  const req = async (u, opt) => {
    const r = await fetch(u, Object.assign({headers:H, credentials:'include'}, opt||{}));
    const t = await r.text();
    let j = null; try { j = JSON.parse(t); } catch(e) {}
    return {status: r.status, json: j, text: t.slice(0, 700)};
  };
  const sleep = ms => new Promise(s => setTimeout(s, ms));
  const b64blob = (b64, type) => {
    const bin = atob(b64); const u8 = new Uint8Array(bin.length);
    for (let i=0;i<bin.length;i++) u8[i] = bin.charCodeAt(i);
    return new Blob([u8], {type: type || 'image/png'});
  };
  const imgSize = async (b64, type) => {
    const im = new Image();
    im.src = 'data:' + (type||'image/png') + ';base64,' + b64;
    await im.decode();
    return [im.naturalWidth, im.naturalHeight];
  };
  // 单次上传：init → PUT → finish，返回 upload id
  const uploadOnce = async (b64, type, w, h) => {
    const ini = await req(S + '/tools/v1/files/uploads', {method:'POST',
      body: JSON.stringify({metadata:{'image-height':String(h),'image-width':String(w)}, scene:'material'})});
    if (ini.status !== 200) return {err:'init ' + ini.status + ' ' + ini.text};
    let pr;
    try {
      pr = await fetch(ini.json.put_url, {method:'PUT', headers: ini.json.headers || {}, body: b64blob(b64, type)});
    } catch(e) { return {err:'put throw ' + String(e)}; }
    if (!pr.ok) return {err:'put ' + pr.status};
    const etag = (pr.headers.get('etag') || '').replace(/"/g, '');
    if (!etag) return {err:'no etag'};
    const fin = await req(S + '/tools/v1/files/uploads/' + ini.json.id + '/finish', {method:'PUT',
      body: JSON.stringify({etag: etag, id: ini.json.id})});
    if (fin.status !== 200) return {err:'finish ' + fin.status + ' ' + fin.text};
    return {id: ini.json.id};
  };

  const out = {op: spec.op};

  // ---------------- 列表 ----------------
  if (spec.op === 'list') {
    let url = MAT + '/elements/personal?pager.pagesz=100';
    let all = [];
    for (let i=0;i<40;i++) {
      const r = await req(url);
      if (r.status !== 200) { out.error = r.status + ' ' + r.text; break; }
      all = all.concat((r.json||{}).elements || []);
      const tok = (r.json||{}).next_page_token;
      if (!tok) break;
      url = MAT + '/elements/personal?pager.pagesz=100&pager.pagetoken=' + encodeURIComponent(tok);
    }
    out.elements = all.map(e => ({
      id: e.id, name: e.name,
      labels: ((e.labels)||[]).map(l => l.name),
      label_ids: ((e.labels)||[]).map(l => l.id),
      modality: e.modality || '', created_at: e.created_at || '',
      voice_id: ((e.voice)||{}).id || '',
      style: (((e.recaption)||{}).style) || '',
      src: ((((e.components)||[])[0])||{}).src_img || ''
    }));
    out.total = out.elements.length;
  }

  // ---------------- 标签 ----------------
  if (spec.op === 'labels') {
    const r = await req(MAT + '/labels');
    out.status = r.status;
    out.labels = ((r.json||{}).labels || []).map(l => ({id: l.id, name: l.name}));
    if (r.status !== 200) out.error = r.text;
  }

  // ---------------- 创建（上传 + 预处理 + 建）----------------
  if (spec.op === 'create') {
    // 1) 标签：确保存在（POST 已存在会报错 ⇒ 回退从列表里找）
    const labelObjs = [];
    for (const nm of (spec.labels || [])) {
      const r = await req(MAT + '/labels', {method:'POST', body: JSON.stringify({name: nm})});
      if (r.status === 200 && r.json && r.json.id) labelObjs.push({id: r.json.id, name: r.json.name});
      else {
        const L = await req(MAT + '/labels');
        const hit = (((L.json)||{}).labels || []).find(x => x.name === nm);
        if (hit) labelObjs.push({id: hit.id, name: hit.name});
        else { out.error = 'label 建不了也找不到: ' + nm + ' ' + r.text; return out; }
      }
    }
    out.labels = labelObjs;

    // 2) 上传参考图（每张两次：content + src_img）
    const comps = [];
    for (let i = 0; i < spec.images.length; i++) {
      const im = spec.images[i];
      const dim = await imgSize(im.b64, im.type);
      const a = await uploadOnce(im.b64, im.type, dim[0], dim[1]);
      if (a.err) { out.error = 'upload#1 ' + JSON.stringify(a); return out; }
      const b = await uploadOnce(im.b64, im.type, dim[0], dim[1]);
      if (b.err) { out.error = 'upload#2 ' + JSON.stringify(b); return out; }
      comps.push({type: i === 0 ? 'main' : 'ref',
                  content: 'ssupload:?id=' + a.id, src_img: 'ssupload:?id=' + b.id,
                  content_type: 'image',
                  selected_region: {top_left:{x:0,y:0}, bottom_right:{x: dim[0], y: dim[1]}}});
    }
    out.components = comps.map(c => ({content: c.content, src_img: c.src_img,
                                      wh: [c.selected_region.bottom_right.x, c.selected_region.bottom_right.y]}));

    // 3) 重名校验
    const ck = await req(MAT + '/elements/create/check?name=' + encodeURIComponent(spec.name));
    out.check = {status: ck.status, text: ck.text.slice(0, 160)};

    // 4) 预处理：平台 AI 读图 → 生成 recaption{style, description}
    const pp = await req(MAT + '/elements/pre-process', {method:'POST', body: JSON.stringify({
      components: comps, labels: labelObjs, name: spec.name, type: 'user',
      voice: {id: spec.voiceId || ''}})});
    out.preprocess = {status: pp.status, text: pp.text.slice(0, 400)};
    if (pp.status !== 200) { out.error = 'pre-process ' + pp.text; return out; }

    // 5) 创建：把 pre-process 的返回合并进 body（服务端返回的字段名以实测为准）
    const pj = pp.json || {};
    const body = {
      name: spec.name, labels: labelObjs, modality: 'image',
      voice: {id: spec.voiceId || '', is_my: false}, type: 'user',
      components: comps, version: '0'
    };
    if (pj.recaption) body.recaption = pj.recaption;
    if (pj.id) body.id = pj.id;
    if (pj.creator_id) body.creator_id = pj.creator_id;
    const cr = await req(MAT + '/elements', {method:'POST', body: JSON.stringify(body)});
    out.create = {status: cr.status, text: cr.text.slice(0, 300),
                  id: (cr.json||{}).id, name: (cr.json||{}).name,
                  labels: (((cr.json)||{}).labels || []).map(l => l.name)};
    if (cr.status !== 200) out.error = 'create ' + cr.text;
    out.done = (cr.status === 200);
  }

  // ---------------- 删除 ----------------
  if (spec.op === 'delete_elements') {
    const res = [];
    for (const id of spec.ids) {
      const r = await req(MAT + '/elements/' + id, {method:'DELETE'});
      res.push({id: id, status: r.status, text: r.text.slice(0, 120)});
      await sleep(spec.gap || 120);
    }
    out.results = res;
    out.deleted = res.filter(x => x.status === 200).length;
  }
  if (spec.op === 'delete_labels') {
    const res = [];
    for (const id of spec.ids) {
      const r = await req(MAT + '/labels/' + id, {method:'DELETE'});
      res.push({id: id, status: r.status, text: r.text.slice(0, 120)});
      await sleep(spec.gap || 120);
    }
    out.results = res;
  }

  return out;
})"""


# ════════════════════════════════════════════════════════════════════════
# Python 侧编排
# ════════════════════════════════════════════════════════════════════════
def call(spec, port=9222, timeout=420):
    """在 vidu 页面上下文里执行一次 API 编排。"""
    with CDP(port=port, url_contains="vidu.cn", timeout=60) as c:
        try:
            c.send("Emulation.setFocusEmulationEnabled", {"enabled": True})  # ★ 后台标签会冻结 fetch 的 promise
            c.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception:
            pass
        return c.eval(JS_CORE + "(%s)" % json.dumps(spec, ensure_ascii=False), timeout=timeout)


def img_spec(path):
    """读图 → base64 + mime。"""
    ext = os.path.splitext(path)[1].lower()
    if ext not in MIME:
        raise ValueError("不支持的图片格式: %s（支持 png/jpg/jpeg/webp）" % ext)
    with open(path, "rb") as f:
        return {"b64": base64.b64encode(f.read()).decode("ascii"), "type": MIME[ext], "path": path}


def fetch_elements(port=9222):
    d = call({"op": "list"}, port=port)
    if d.get("error"):
        raise RuntimeError("拉主体列表失败: %s" % d["error"])
    return d.get("elements") or []


def fetch_labels(port=9222):
    d = call({"op": "labels"}, port=port)
    if d.get("error"):
        raise RuntimeError("拉标签失败: %s" % d["error"])
    return d.get("labels") or []


# ---------------- 子命令 ----------------
def cmd_list(a):
    els = fetch_elements(a.port)
    if a.label:
        els = [e for e in els if a.label in (e.get("labels") or [])]
    if a.json:
        print(json.dumps(els, ensure_ascii=False, indent=1))
        return 0
    print("主体库共 %d 条%s" % (len(els), ("（筛标签 %s）" % a.label) if a.label else ""))
    print("%-22s | %-12s | %-14s | %s" % ("名字", "id", "标签", "风格"))
    print("-" * 78)
    for e in sorted(els, key=lambda x: (x.get("labels") or [""])[0], reverse=True):
        tags = ",".join(e.get("labels") or []) or "—"
        print("%-22s | %-12s | %-14s | %s" % (e.get("name", "")[:20], e.get("id", ""), tags[:12],
                                              e.get("style", "")[:16]))
    return 0


def cmd_labels(a):
    for l in fetch_labels(a.port):
        print("%-16s %s" % (l.get("name"), l.get("id")))
    return 0


def cmd_create(a):
    imgs = [img_spec(p) for p in a.image]
    spec = {"op": "create", "name": a.name, "images": imgs,
            "labels": a.label or [], "voiceId": a.voice or ""}
    d = call(spec, port=a.port)
    if a.json:
        print(json.dumps({k: v for k, v in d.items() if k != "components"}, ensure_ascii=False, indent=1))
    print("预处理:", (d.get("preprocess") or {}).get("status"),
          (d.get("preprocess") or {}).get("text", "")[:120])
    cr = d.get("create") or {}
    if d.get("done"):
        print("★ 已创建 #%s %s 标签=%s" % (cr.get("id"), cr.get("name"), cr.get("labels")))
        log({"mode": "refs_create", "name": a.name, "id": cr.get("id"), "labels": a.label})
    else:
        print("✗ 创建失败:", d.get("error") or cr.get("text"))
        log({"mode": "refs_create", "name": a.name, "error": d.get("error") or cr.get("text")})
        return 1
    return 0


def cmd_import(a):
    man = json.load(open(a.manifest, encoding="utf-8"))
    label = a.label or man.get("label")
    items = man.get("items") or []
    if not label:
        print("★ manifest 缺 label（或用 --label 指定）——「每部剧资产打剧名标签」是硬规则")
        return 1
    print("manifest: %s ｜ 剧名标签=%s ｜ %d 个主体" % (a.manifest, label, len(items)))
    exist = {e["name"] for e in fetch_elements(a.port)}
    plan, skip = [], []
    for it in items:
        nm = it.get("name")
        imgs = it.get("images") or ([it["image"]] if it.get("image") else [])
        miss = [p for p in imgs if not os.path.exists(p)]
        if not imgs or miss:
            skip.append((nm, "缺图 %s" % miss))
            continue
        if nm in exist and not a.force:
            skip.append((nm, "已存在同名主体（--force 覆盖）"))
            continue
        plan.append((nm, imgs))
    for nm, why in skip:
        print("  · 跳过 %-18s %s" % (nm, why))
    print("待建 %d 个" % len(plan))
    if a.dry_run:
        for nm, imgs in plan:
            print("  → %-18s %d 张: %s" % (nm, len(imgs), [os.path.basename(p) for p in imgs]))
        print("★ dry-run，未提交")
        return 0
    ok = fail = 0
    for nm, imgs in plan:
        d = call({"op": "create", "name": nm, "images": [img_spec(p) for p in imgs],
                  "labels": [label], "voiceId": ""}, port=a.port)
        if d.get("done"):
            ok += 1
            print("  ✓ %-18s #%s" % (nm, (d.get("create") or {}).get("id")))
        else:
            fail += 1
            print("  ✗ %-18s %s" % (nm, d.get("error") or (d.get("create") or {}).get("text")))
        time.sleep(a.pause)
    print("\n建 %d ｜ 跳过 %d ｜ 失败 %d" % (ok, len(skip), fail))
    log({"mode": "refs_import", "label": label, "ok": ok, "skip": len(skip), "fail": fail})
    return 0 if not fail else 1


def _select(elements, a):
    sel = []
    if a.ids:
        want = {s.strip() for s in a.ids.split(",") if s.strip()}
        sel = [e for e in elements if e["id"] in want]
    elif a.names:
        want = {s.strip() for s in a.names.split(",") if s.strip()}
        sel = [e for e in elements if e["name"] in want]
    elif a.label:
        sel = [e for e in elements if a.label in (e.get("labels") or [])]
    else:
        print("★ 必须指定 --ids / --names / --label 之一（拒绝无差别清库）")
        return None
    # ★ 保留标签保护：用户常用主体永不被清
    kept = [e for e in sel if a.keep_label in (e.get("labels") or [])]
    sel = [e for e in sel if a.keep_label not in (e.get("labels") or [])]
    return sel, kept


def cmd_delete(a):
    elements = fetch_elements(a.port)
    r = _select(elements, a)
    if r is None:
        return 1
    sel, kept = r
    print("选中 %d 条；保护（带「%s」标签）%d 条" % (len(sel), a.keep_label, len(kept)))
    for e in kept:
        print("  🛡 %-20s #%s  ← 永不删" % (e.get("name"), e["id"]))
    for e in sel:
        print("  - %-20s #%s 标签=%s" % (e.get("name"), e["id"], ",".join(e.get("labels") or [])))
    if not sel:
        print("没有可删的")
        return 0
    if not a.yes:
        print("★ 预演（未删）。确认真删请加 --yes")
        return 0
    ids = [e["id"] for e in sel]
    d = call({"op": "delete_elements", "ids": ids, "gap": a.gap}, port=a.port)
    okn = d.get("deleted", 0)
    bad = [x for x in (d.get("results") or []) if x.get("status") != 200]
    print("删除 %d/%d" % (okn, len(ids)))
    if bad:
        print("失败:", json.dumps(bad, ensure_ascii=False))
    log({"mode": "refs_delete", "n": okn, "ids": ids[:200]})
    return 0 if not bad else 1


def cmd_delete_labels(a):
    labs = fetch_labels(a.port)
    if a.names:
        want = {s.strip() for s in a.names.split(",") if s.strip()}
        sel = [l for l in labs if l["name"] in want]
    else:
        print("★ 需要 --names"); return 1
    for l in sel:
        print("  - %s #%s" % (l["name"], l["id"]))
    if not sel:
        return 0
    if not a.yes:
        print("★ 预演（未删）。加 --yes 真删")
        return 0
    d = call({"op": "delete_labels", "ids": [l["id"] for l in sel]}, port=a.port)
    print("删除结果:", json.dumps(d.get("results"), ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Vidu 主体库批量管理（纯 API）")
    ap.add_argument("--port", type=int, default=9222)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list"); p.add_argument("--label", default=""); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("labels"); p.set_defaults(fn=cmd_labels)

    p = sub.add_parser("create")
    p.add_argument("--name", required=True)
    p.add_argument("--image", action="append", required=True, help="可重复；第一张=主要参考")
    p.add_argument("--label", action="append", default=[], help="可重复（剧名标签）")
    p.add_argument("--voice", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_create)

    p = sub.add_parser("import")
    p.add_argument("--manifest", required=True)
    p.add_argument("--label", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--pause", type=float, default=1.0)
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("delete")
    p.add_argument("--ids", default=""); p.add_argument("--names", default="")
    p.add_argument("--label", default="")
    p.add_argument("--keep-label", default=KEEP_LABEL_DEFAULT)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--gap", type=int, default=120)
    p.set_defaults(fn=cmd_delete)

    p = sub.add_parser("delete-labels")
    p.add_argument("--names", required=True)
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_delete_labels)

    a = ap.parse_args()
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
