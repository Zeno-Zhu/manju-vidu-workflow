# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:c3b8b10200 · 溯源标记，请保留
"""vidu_image.py —— Vidu 出图：**文生图** ＋ **参考图生图**（★ 资产能力补齐）。

═══ 它解决什么 ═══
资产（角色基准图 / 角色设定版 / 场景 / 道具）用户可能没有 ⇒ 直接用 Vidu 生成。
两条线都**纯 API**（不点 UI），提交 → 轮询 → **下载无水印 PNG**。

═══ 实测接口（2026-10-13，base `https://service.vidu.cn`）═══
  ★ 出图**也是 task**，与视频同一个提交端点：
  POST /vidu/v1/tasks
  ├─ 文生图    type = "text2image"
  └─ 参考图生图 type = "reference2image"
  headers: X-Platform: web / X-App-Version: - / Content-Type: application/json

  **文生图** body：
  {"input":{"prompts":[{"type":"text","content":"…"}]},"type":"text2image",
   "settings":{"duration":0,"resolution":"1080p","aspect_ratio":"16:9","sample_count":1,
               "movement_amplitude":"auto","schedule_mode":"normal",
               "model_version":"3.2_image_2_5_fast","use_trial":true}}

  **参考图生图** body（多一条 image 条目）：
  {"input":{"prompts":[
       {"type":"text","content":"…[@图1]"},
       {"type":"image","content":"ssupload:?id=<裁剪图>",
        "src_imgs":["ssupload:?id=<原图>"],          ← ★ 注意是 **src_imgs（数组）**
        "selected_region":{"top_left":{…},"bottom_right":{…}}, "name":"图1"}]},
   "type":"reference2image",
   "settings":{…, "image_count":1, …}}

  **查询**：GET /vidu/v1/tasks/history/me?pager.pagesz=N&scene_mode=none&types=text2image
           （**必须带 types 过滤**，否则混进视频任务）
  **下载**：creations[].download_uri = 无水印 image.png（`uri` 是 watermarked.png）
           ★ 与视频一样，auth_key 自带签名，**裸 HTTP 即可**，无需 cookie。

═══ 模型（实测三个）═══
  | 别名 | 显示名 | model_version |
  |---|---|---|
  | `fast` | 全能 Image 2.5 Fast | `3.2_image_2_5_fast` |
  | `pro`  | 全能 Image 2.5 Pro  | `3.2_image_2_5_pro` |
  | `base` | 全能 Image（灵感级画质） | `3.2_image_2` |

═══ 用法 ═══
  PY="C:/Users/Administrator/AppData/Local/Microsoft/WindowsApps/python.exe"
  S="C:/Users/Administrator/.workbuddy/skills/manju-vidu/scripts"

  # 文生图
  $PY $S/vidu_image.py t2i --prompt "一位年轻女性的角色设定板，多视角，中性灰背景" \
                           --model pro --ratio 16:9 --out "<项目>/01_资产库/图片"
  # 参考图生图（可多张参考图）
  $PY $S/vidu_image.py r2i --prompt "保持人物外貌与服装不变，改成坐在窗边看书" \
                           --ref "<项目>/01_资产库/角色基准图/XX.png" --out "<项目>/01_资产库/图片"
  # 批量（每行一条提示词）
  $PY $S/vidu_image.py t2i --prompt-file "<项目>/资产提示词.txt" --model fast --out "<项目>/01_资产库/图片"
  $PY $S/vidu_image.py models

⚠ 系统 Python 3.14 + **沙箱外**执行；浏览器实例要先起（`vidu_edge.py up`）。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vidu_cdp import CDP   # noqa: E402
from vidu_log import log   # noqa: E402

MODELS = {
    "fast": "3.2_image_2_5_fast",
    "pro": "3.2_image_2_5_pro",
    "base": "3.2_image_2",
}
MODEL_NAME = {v: k for k, v in MODELS.items()}
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
RE_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t]+')

# ════════════════════════════════════════════════════════════════════
JS = r"""(async (spec) => {
  const S = 'https://service.vidu.cn';
  const H = {'X-Platform':'web','X-App-Version':'-','Content-Type':'application/json'};
  const out = {op: spec.op};
  const jp = async (r) => { const t = await r.text(); try { return {j: JSON.parse(t)}; }
                            catch(e) { return {err: 'HTTP ' + r.status + ' ' + t.slice(0, 200)}; } };
  const blobOf = (b64, type) => { const bin = atob(b64); const u8 = new Uint8Array(bin.length);
    for (let i=0;i<bin.length;i++) u8[i] = bin.charCodeAt(i); return new Blob([u8], {type}); };
  const imgWH = async (b64, type) => { const im = new Image();
    im.src = 'data:' + (type||'image/png') + ';base64,' + b64; await im.decode();
    return [im.naturalWidth, im.naturalHeight]; };
  // scene='vidu' 是出图/出视频的统一上传场景（实测）
  const upImg = async (b64, type) => {
    const dim = await imgWH(b64, type);
    const r1 = await fetch(S + '/tools/v1/files/uploads', {method:'POST', headers:H, credentials:'include',
      body: JSON.stringify({metadata:{'image-height':String(dim[1]),'image-width':String(dim[0])}, scene:'vidu'})});
    const p1 = await jp(r1); if (p1.err) return {err:'init ' + p1.err};
    const pr = await fetch(p1.j.put_url, {method:'PUT', headers: p1.j.headers||{}, body: blobOf(b64, type)});
    if (!pr.ok) return {err:'put ' + pr.status};
    const etag = (pr.headers.get('etag')||'').replace(/"/g,'');
    const r3 = await fetch(S + '/tools/v1/files/uploads/' + p1.j.id + '/finish', {method:'PUT', headers:H,
      credentials:'include', body: JSON.stringify({etag, id: p1.j.id})});
    const p3 = await jp(r3); if (p3.err) return {err:'finish ' + p3.err};
    return {id: p1.j.id, uri: (p3.j||{}).uri || ('ssupload:?id=' + p1.j.id), w: dim[0], h: dim[1]};
  };

  if (spec.op === 'models') {
    const r = await fetch(S + '/credit/v1/credits/me', {headers:H, credentials:'include'});
    const p = await jp(r);
    out.credits = (p.j||{}).credits;
    out.list = spec.cands || [];
    return out;
  }

  if (spec.op === 'submit') {
    const prompts = [{type:'text', content: spec.text}];
    out.refs = [];
    for (let i = 0; i < (spec.refs || []).length; i++) {
      const rf = spec.refs[i];
      // 每张参考图传两次：content（裁剪图）+ src_imgs 里那张（原图）
      const a = await upImg(rf.b64, rf.type);
      if (a.err) { out.error = 'ref#' + (i+1) + ' ' + a.err; return out; }
      const b = await upImg(rf.b64, rf.type);
      if (b.err) { out.error = 'ref#' + (i+1) + ' src ' + b.err; return out; }
      prompts.push({type:'image', content: a.uri, src_imgs: [b.uri],
                    selected_region: {top_left:{x:0,y:0}, bottom_right:{x:a.w, y:a.h}},
                    name: '图' + (i+1)});
      out.refs.push({i: i+1, content: a.uri, src: b.uri});
    }
    const st = Object.assign({}, spec.settings);
    if ((spec.refs || []).length) st.image_count = spec.refs.length;
    const body = {input:{prompts: prompts, editor_mode:'normal', enhance:true},
                  type: spec.refs && spec.refs.length ? 'reference2image' : 'text2image',
                  settings: st};
    out.body = {type: body.type, model_version: st.model_version, nPrompts: prompts.length};
    const r = await fetch(S + '/vidu/v1/tasks', {method:'POST', headers:H, credentials:'include',
      body: JSON.stringify(body)});
    const p = await jp(r);
    out.status = r.status;
    if (p.j) out.task_id = p.j.id; else out.error = p.err;
    return out;
  }

  if (spec.op === 'poll') {
    const r = await fetch(S + '/vidu/v1/tasks/history/me?pager.pagesz=10&scene_mode=none&types='
                          + spec.kind, {headers:H, credentials:'include'});
    const p = await jp(r); if (p.err) { out.error = p.err; return out; }
    const t = (p.j.tasks || []).find(x => x.id === spec.task_id) || (p.j.tasks || [])[0];
    if (!t) { out.error = 'no-task'; return out; }
    out.task = {id: t.id, state: t.state, err_code: t.err_code||'', err_msg: t.err_msg||'',
      creations: (t.creations||[]).map(c => ({id: c.id,
        download_uri: c.download_uri||'', nomark_uri: c.nomark_uri||'',
        uri: c.uri||'', cover_uri: c.cover_uri||''}))};
    return out;
  }
  return out;
})"""


def call(spec, port=9222, timeout=600):
    with CDP(port=port, url_contains="vidu.cn", timeout=60) as c:
        try:
            c.send("Emulation.setFocusEmulationEnabled", {"enabled": True})  # ★ 只让页面"以为"有焦点，不抢窗口焦点
            c.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception:
            pass
        return c.eval(JS + "(%s)" % json.dumps(spec, ensure_ascii=False), timeout=timeout)


def img_spec(path):
    ext = os.path.splitext(path)[1].lower()
    if ext not in MIME:
        raise ValueError("不支持的图片格式: %s" % path)
    with open(path, "rb") as f:
        b = base64.b64encode(f.read()).decode("ascii")
    return {"b64": b, "type": MIME[ext], "path": path}


def build_settings(model, ratio, resolution="1080p", count=1, use_trial=True):
    """出图的 settings：★ `duration` 恒为 0（图片没有时长）。"""
    return {"duration": 0, "resolution": resolution, "aspect_ratio": ratio,
            "sample_count": int(count), "movement_amplitude": "auto",
            "schedule_mode": "normal", "model_version": MODELS.get(model, model),
            "use_trial": bool(use_trial)}


def http_get(url, timeout=120):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with op.open(req, timeout=timeout) as r:
        return r.read()


def ext_of(data):
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:4] == b"RIFF":
        return ".webp"
    if len(data) > 12 and data[4:8] == b"ftyp":
        return ".mp4"
    return ".png"


def pick_url(creations):
    """★ 只认**无水印**地址。返回 (url, kind)：kind = 'clean' | 'watermarked' | None。

    ⚠ 实测（2026-10-13）：`uri` = `watermarked.png`（右下角有「Vidu AI 生成」水印），
      `download_uri` / `nomark_uri` = `image.png`（**无水印**）。
      平台**先置 success、稍后才挂无水印文件** ⇒ `download_uri` 可能暂时为空。
      ⛔ **绝不能**在这时退到 `uri` —— 那会静默下到带水印的版本。
    """
    for c in creations or []:
        for k in ("download_uri", "nomark_uri"):
            if c.get(k):
                return c[k], "clean"
    for c in creations or []:
        if c.get("uri"):
            return c["uri"], "watermarked"
    return None, None


def wait_and_download(task_id, kind, out_path, port=9222, interval=6, max_wait=600,
                      allow_watermark=False):
    """轮询到 success 并下载**无水印**图。返回落盘路径。

    ★ 中途若只有水印版地址（中间态），**继续等**而不是将就下 —— 最多等到 max_wait。
    `allow_watermark=True` 时才允许退到水印版（并会打印告警）。
    """
    t0 = time.time()
    warned = False
    while time.time() - t0 < max_wait:
        d = call({"op": "poll", "task_id": task_id, "kind": kind}, port=port, timeout=120)
        if d.get("error"):
            raise RuntimeError("查任务失败: %s" % d["error"])
        t = d.get("task") or {}
        st = t.get("state")
        if st == "failed":
            raise RuntimeError("生成失败: %s %s" % (t.get("err_code"), t.get("err_msg")))
        if st == "success":
            u, which = pick_url(t.get("creations"))
            if u and which == "clean":
                data = http_get(u)
                p = out_path + ext_of(data)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(data)
                return p
            if u and which == "watermarked":
                if not warned:
                    print("   ⚠ 只有水印版地址（中间态），继续等无水印文件…")
                    warned = True
                if allow_watermark and time.time() - t0 > max_wait * 0.5:
                    data = http_get(u)
                    p = out_path + ext_of(data)
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p, "wb") as f:
                        f.write(data)
                    print("   ⚠⚠ 已下载**带水印**版本（--allow-watermark 开启）")
                    return p
            # 有 success 但两个地址都空 —— 也是中间态，继续等
        time.sleep(interval)
    raise RuntimeError("等超时（%ds）：任务在生成中或无水印文件未就绪；稍后重跑同一命令即可" % max_wait)


def safe_name(s, n=40):
    return RE_UNSAFE.sub("_", (s or "").strip())[:n] or "img"


def run_batch(prompts, args, kind):
    """prompts: [(标题, 文本)]；kind: text2image|reference2image。返回 (ok, fail)。"""
    refs = [img_spec(p) for p in (args.ref or [])]
    ok = fail = 0
    for i, (title, text) in enumerate(prompts, 1):
        spec = {"op": "submit", "text": text, "refs": refs,
                "settings": build_settings(args.model, args.ratio, args.resolution,
                                           args.count, not args.no_trial)}
        if args.dry_run:
            print("[%d/%d] %s" % (i, len(prompts), title))
            print("      model=%s(%s) ratio=%s  refs=%d\n      %s"
                  % (args.model, MODELS.get(args.model), args.ratio, len(refs), text[:80]))
            continue
        t0 = time.time()
        d = call(spec, port=args.port)
        if d.get("status") != 200:
            fail += 1
            print("[%d/%d] %s ✗ %s" % (i, len(prompts), title, d.get("error")))
            log({"mode": "img_submit", "title": title, "error": str(d.get("error"))[:300]})
            continue
        tid = d.get("task_id")
        try:
            p = wait_and_download(tid, kind, os.path.join(args.out, safe_name(title)),
                                  port=args.port, interval=args.interval, max_wait=args.max_wait,
                                  allow_watermark=args.allow_watermark)
            ok += 1
            print("[%d/%d] %s ✓ %s  (%.0fs)" % (i, len(prompts), title, p, time.time() - t0))
            log({"mode": "img_done", "title": title, "task": tid, "file": p})
        except Exception as e:
            fail += 1
            print("[%d/%d] %s ✗ %s" % (i, len(prompts), title, str(e)[:160]))
            log({"mode": "img_fail", "title": title, "task": tid, "error": str(e)[:200]})
        time.sleep(args.pause)
    return ok, fail


def read_prompts(args):
    """返回 [(标题, 文本)]。优先 --prompt-file（每行一条，`标题||正文` 可带标题）。"""
    if args.prompt_file:
        out = []
        with open(args.prompt_file, encoding="utf-8") as f:
            for ln in f:
                s = ln.strip()
                if not s or s.startswith("#"):
                    continue
                if "||" in s:
                    t, b = s.split("||", 1)
                    out.append((t.strip(), b.strip()))
                else:
                    out.append((s[:30], s))
        return out
    return [(args.name or (args.prompt[:30]), args.prompt)]


def main():
    ap = argparse.ArgumentParser(description="Vidu 出图（文生图 / 参考图生图）")
    ap.add_argument("cmd", choices=["t2i", "r2i", "models"])
    ap.add_argument("--prompt", default="")
    ap.add_argument("--prompt-file", default="", help="每行一条；`标题||正文` 可指定文件名")
    ap.add_argument("--name", default="", help="单条时的输出文件名")
    ap.add_argument("--ref", action="append", default=[], help="参考图（r2i 用，可重复）")
    ap.add_argument("--model", default="fast", help="fast / pro / base，或直接给 model_version")
    ap.add_argument("--ratio", default="16:9")
    ap.add_argument("--resolution", default="1080p")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--out", default="", help="输出目录（必填，除 models）")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--interval", type=float, default=6.0)
    ap.add_argument("--max-wait", type=float, default=600.0)
    ap.add_argument("--pause", type=float, default=1.0)
    ap.add_argument("--no-trial", action="store_true")
    ap.add_argument("--allow-watermark", action="store_true",
                    help="★ 默认**只下无水印**；开启后才允许在等不到无水印时退到带水印版")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.cmd == "models":
        print("可用模型别名 → model_version：")
        for k, v in MODELS.items():
            print("  %-5s %s" % (k, v))
        d = call({"op": "models", "cands": list(MODELS)}, port=a.port, timeout=90)
        print("  当前积分:", d.get("credits"))
        return 0

    if not a.out:
        print("★ 必须给 --out <输出目录>")
        return 1
    if a.cmd == "r2i" and not a.ref:
        print("★ r2i 必须给 --ref <参考图>（可多张）")
        return 1
    if a.cmd == "t2i" and a.ref:
        print("⚠ t2i 忽略 --ref（要用参考图请走 r2i）")

    prompts = read_prompts(a)
    if not prompts:
        print("★ 没有提示词")
        return 1
    kind = "reference2image" if a.cmd == "r2i" else "text2image"
    print("出图：%s ｜ %d 条 ｜ model=%s(%s) ｜ ratio=%s %s ｜ refs=%d"
          % (kind, len(prompts), a.model, MODELS.get(a.model, "自定义"),
             a.ratio, a.resolution, len(a.ref or [])))
    ok, fail = run_batch(prompts, a, kind)
    print("\n完成 %d ｜ 失败 %d ｜ 输出目录 %s" % (ok, fail, a.out))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
