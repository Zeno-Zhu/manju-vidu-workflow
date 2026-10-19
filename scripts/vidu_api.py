# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:9edc210c1e · 溯源标记，请保留
"""vidu_api.py —— Vidu 任务**直连提交**（不点 UI）+ 额度查询。

═══ 为什么需要它 ═══
走编辑器提交一组要 5–8 秒 UI 操作 ⇒ 多账号 / 100 并发 / 每天 200 条**根本不可行**。
本模块把提交压成**一次 POST**（< 1 秒），实测 2026-10-13 打通：

  POST https://service.vidu.cn/vidu/v1/tasks
  headers: X-Platform: web / X-App-Version: - / Content-Type: application/json
  body: {"input":{"prompts":[{"type":"text","content":"… [@图1] …"},
                              {"type":"image","content":"ssupload:?id=<upload_id>"}],
                  "editor_mode":"normal","enhance":true},
         "type":"character2video",
         "settings":{"duration":5,"resolution":"1080p","movement_amplitude":"auto",
                     "aspect_ratio":"9:16","sample_count":1,
                     "schedule_mode":"nopeak",           # ★ nopeak=闲时 0 积分 / normal=消耗积分
                     "codec":"h265","model_version":"3.4","use_trial":false}}

★★ 资源引用一律是 **`ssupload:?id=<upload_id>`**，不是 URL：
   · 图片：`{"type":"image","content":"ssupload:?id=…"}`（scene="vidu"）
   · 主体：`{"type":"material","content":"<主体图URL>","name":"…","material":{"id":…,"version":…}}`
   · 音频：⚠ 见 §音频（PUT 被 CORS 拦，暂未打通）

═══ 上传三步（scene：图片="vidu"，主体="material"）═══
  POST /tools/v1/files/uploads {"metadata":{…},"scene":…} → {id, put_url, headers}
  PUT  <put_url>  （body=原始字节，带 init.headers）
  PUT  /tools/v1/files/uploads/{id}/finish  {"etag":…,"id":…} → {"uri":"ssupload:?id=…"}

═══ 用法 ═══
  PY="C:/Users/Administrator/AppData/Local/Microsoft/WindowsApps/python.exe"
  S="C:/Users/Administrator/.workbuddy/skills/manju-vidu/scripts"

  $PY $S/vidu_api.py quota                                   # 积分 / 并发
  $PY $S/vidu_api.py elements                                # 主体列表（简）
  $PY $S/vidu_api.py run --jobs <jobs_vidu.json> --job 1_1_标准 --mode nopeak --dry-run
  $PY $S/vidu_api.py run --jobs <jobs_vidu.json> --all --mode nopeak --limit 5

⚠ 系统 Python 3.14 + **沙箱外**执行。
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vidu_cdp import CDP   # noqa: E402
from vidu_log import log   # noqa: E402

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
AMIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".aiff": "audio/aiff",
         ".aif": "audio/aiff", ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac"}
RE_PN = re.compile(r"(pn|vm)(\d+),([^，,=]*?)=\s*@")

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

  // ---- 通用上传（scene=vidu）：init → PUT → finish，返回 upload id ----
  // ★★ 关键：metadata 的字段名直接决定 init 回给 PUT 的 `x-oss-meta-*` 头，
  //    而 OSS 的 CORS 只放行**白名单内**的头 ⇒ 字段名写错 = PUT 报 `Failed to fetch`。
  //      图片：{"image-height","image-width"}  音频：{"duration"}   ← 2026-10-13 抓包实测
  const upFile = async (b64, ctype, meta) => {
    const r1 = await fetch(S + '/tools/v1/files/uploads', {method:'POST', headers:H, credentials:'include',
      body: JSON.stringify({metadata: meta, scene:'vidu'})});
    const p1 = await jp(r1); if (p1.err) return {err:'init ' + p1.err};
    const hd = Object.assign({}, p1.j.headers||{});
    hd['Content-Type'] = ctype;                       // ★ 必须显式给，页面就是这么发的
    let pr;
    try { pr = await fetch(p1.j.put_url, {method:'PUT', headers: hd, body: blobOf(b64, ctype)}); }
    catch(e) { return {err:'put throw ' + String(e).slice(0,70)}; }
    if (!pr.ok) return {err:'put ' + pr.status};
    const etag = (pr.headers.get('etag')||'').replace(/"/g,'');
    const r3 = await fetch(S + '/tools/v1/files/uploads/' + p1.j.id + '/finish', {method:'PUT', headers:H,
      credentials:'include', body: JSON.stringify({etag: etag, id: p1.j.id})});
    const p3 = await jp(r3); if (p3.err) return {err:'finish ' + p3.err};
    return {id: p1.j.id, uri: (p3.j||{}).uri || ('ssupload:?id=' + p1.j.id), init_headers: p1.j.headers};
  };
  const upImg = async (b64, type) => {
    const dim = await imgWH(b64, type);
    const r = await upFile(b64, type,
      {'image-height':String(dim[1]),'image-width':String(dim[0])});
    if (!r.err) r.wh = dim;
    return r;
  };
  const upAud = async (b64, type, dur) => upFile(b64, type, {duration: String(dur)});

  if (spec.op === 'quota') {
    // ★★ 闲时额度（每天 200 条）**在这里** —— 页面左下角「用量 used/total」就是它：
    //    GET /vidu/v1/tasks/quota → {"nopeak_quota":{"used":200,"total":200},"claw_pass_quota":null}
    //    ⛔ 别再用 /credit/v1/credits/me 猜额度 —— 那是**积分**，完全是另一回事（踩过）。
    const rq = await fetch(S + '/vidu/v1/tasks/quota', {headers: H, credentials:'include'});
    const pq = await jp(rq);
    const qj = pq.j || {};
    out.nopeak_quota = qj.nopeak_quota || null;       // {used, total}
    out.claw_pass_quota = qj.claw_pass_quota || null;
    out.quota_raw = qj || pq.err;
    out.nopeak_left = qj.nopeak_quota
        ? Math.max(0, (qj.nopeak_quota.total || 0) - (qj.nopeak_quota.used || 0)) : null;
    // 积分（另一回事，顺带取）
    const r = await fetch(S + '/credit/v1/credits/me', {headers: H, credentials:'include'});
    const p = await jp(r); out.quota = p.j || p.err;
  }

  if (spec.op === 'state') {
    // ★ 调度器用：数「平台侧进行中」+「今天已用闲时」。
    //   翻页用**页码式** `pager.pagesz=100&pager.page=N`（2026-10-13 实测；
    //   只写 pager.page=N 时每页仅 10 条，created_at 游标不可靠）。
    const all = [];
    for (let page = 1; page <= (spec.maxPages || 20); page++) {
      const r = await fetch(S + '/vidu/v1/tasks/history/me?pager.pagesz=100&pager.page=' + page,
                            {headers:H, credentials:'include'});
      const p = await jp(r);
      if (p.err) { out.error = p.err; break; }
      const ts = (p.j||{}).tasks || [];
      if (!ts.length) break;
      all.push.apply(all, ts);
      if (ts.length < 100) break;
    }
    const INFLIGHT = ['created','preparation','processing'];
    const infl = all.filter(t => INFLIGHT.indexOf(t.state) >= 0);
    const pre = spec.todayPrefix || '';      // 形如 "2026-10-13"
    const todayNopeak = all.filter(t => (t.created_at||'').indexOf(pre) === 0
                                   && ((t.settings||{}).schedule_mode === 'nopeak'));
    out.total_tasks = all.length;
    out.inflight = infl.length;
    out.inflight_ids = infl.map(t => t.id);
    out.today_nopeak = todayNopeak.length;
    out.today_prefix = pre;
    out.by_state = all.reduce((m,t) => { m[t.state] = (m[t.state]||0)+1; return m; }, {});
  }

  if (spec.op === 'elements') {
    const r = await fetch(S + '/vidu/v1/material/elements/personal?pager.pagesz=100',
                          {headers:H, credentials:'include'});
    const p = await jp(r);
    out.elements = ((p.j||{}).elements || []).map(e => ({id:e.id, name:e.name, version:e.version,
      content:((e.components||[])[0]||{}).content, labels:((e.labels)||[]).map(l=>l.name)}));
  }

  if (spec.op === 'upload') {
    const res = [];
    for (const im of spec.images) {
      const u = await upImg(im.b64, im.type);
      res.push(Object.assign({name: im.name, path: im.path}, u));
    }
    out.uploads = res;
  }

  if (spec.op === 'submit') {
    // 1) 上传图片（顺序 = 图1..图N）
    const uploads = [];
    for (const im of (spec.images || [])) {
      const u = await upImg(im.b64, im.type);
      if (u.err) { out.error = '图 ' + im.name + ' 上传失败: ' + u.err; return out; }
      uploads.push(u);
    }
    out.uploads = uploads.map(u => ({id: u.id, uri: u.uri}));
    // 1b) 上传音频（顺序 = 音频1..音频N；上限 3）
    const auds = [];
    for (const au of (spec.audios || []).slice(0, 3)) {
      const u = await upAud(au.b64, au.type, au.dur);
      if (u.err) { out.error = '音 ' + au.name + ' 上传失败: ' + u.err; return out; }
      auds.push(u);
    }
    out.audio_uploads = auds.map(u => ({id: u.id, uri: u.uri}));
    // 2) 组装 prompts：文本 + image/material/audio 条目（顺序必须与 [@图N]/[@音频N] 对齐）
    const prompts = [{type:'text', content: spec.text}];
    for (const u of uploads) prompts.push({type:'image', content: u.uri});
    for (const s of (spec.subjects || [])) {
      prompts.push({type:'material', content: s.content, name: s.name,
                    material: {id: s.id, version: s.version}});
    }
    for (const u of auds) prompts.push({type:'audio', content: u.uri});
    const body = {input:{prompts: prompts, editor_mode:'normal', enhance:true},
                  type:'character2video', settings: spec.settings};
    out.request = {nPrompts: prompts.length, settings: spec.settings,
                   promptHead: spec.text.slice(0, 120)};
    const r = await fetch(S + '/vidu/v1/tasks', {method:'POST', headers:H, credentials:'include',
      body: JSON.stringify(body)});
    const p = await jp(r);
    out.status = r.status;
    if (p.j) { out.task_id = p.j.id; out.settings_echo = p.j.settings || null; }
    else { out.error = p.err; }
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


def img_spec(path, name=None):
    ext = os.path.splitext(path)[1].lower()
    if ext not in MIME:
        raise ValueError("不支持的图片格式: %s" % path)
    with open(path, "rb") as f:
        b = base64.b64encode(f.read()).decode("ascii")
    return {"b64": b, "type": MIME[ext], "name": name or os.path.splitext(os.path.basename(path))[0],
            "path": path}


def audio_spec(path, name=None):
    """音频 → base64 + **时长（秒，向上取整）**。

    ★★ 时长必须放进上传 metadata 的 `duration` 字段（2026-10-13 抓包实测）：
       少了它 ⇒ 提交时报 `audio duration must be between 1 and 16 seconds`。
       （页面前端也是这么干的：`PUT` 头带 `x-oss-meta-duration`。）
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in AMIME:
        raise ValueError("不支持的音频格式: %s（支持 %s）" % (path, "/".join(AMIME)))
    with open(path, "rb") as f:
        b = base64.b64encode(f.read()).decode("ascii")
    dur = 5.0
    if ext == ".wav":
        try:
            with wave.open(path) as w:
                dur = w.getnframes() / float(w.getframerate())
        except Exception:
            pass
    return {"b64": b, "type": AMIME[ext],
            "name": name or os.path.splitext(os.path.basename(path))[0],
            "dur": max(1, int(math.ceil(dur))), "path": path}


def render_prompt(text):
    """`pn1,二丫少女=@` → `pn1,二丫少女[@图1]`；`vm1,xx=@` → `vm1,xx[@音频1]`。

    与编辑器里的渲染一致（保留盘点前缀 + 追加 mention 标记）。
    """
    return RE_PN.sub(lambda m: "%s%s,%s[@%s%s]" % (
        m.group(1), m.group(2), m.group(3),
        "图" if m.group(1) == "pn" else "音频", m.group(2)), text)


def build_settings(duration, ratio, mode="nopeak", resolution="1080p"):
    return {"duration": int(duration), "resolution": resolution, "movement_amplitude": "auto",
            "aspect_ratio": ratio, "sample_count": 1,
            "schedule_mode": "nopeak" if mode == "nopeak" else "normal",
            "codec": "h265", "model_version": "3.4", "use_trial": False}


# ---------------- 子命令 ----------------
def cmd_quota(a):
    d = call({"op": "quota"}, port=a.port, timeout=90)
    nq = d.get("nopeak_quota") or {}
    q = d.get("quota") or {}
    if nq:
        used, total = nq.get("used"), nq.get("total")
        print("★ 闲时额度：已用 %s / %s　剩余 %s" % (used, total, d.get("nopeak_left")))
        if d.get("nopeak_left") == 0:
            print("  ⛔ 今天的闲时额度**已用完** —— 出片会停在门禁处；"
                  "换账号（`vidu_edge.py up --account 账号2`）或等明天 0 点重置。")
    else:
        print("⚠ 没读到 nopeak_quota（页面可能不在创作页 → 先 `vidu_edge.py up`）")
    if isinstance(q, dict) and "credits" in q:
        print("  积分：credits=%s free=%s subscribed=%s concurrency=%s"
              % (q.get("credits"), q.get("credits_free"),
                 q.get("credits_subscribed"), q.get("concurrency")))
    else:
        print("  积分：%s" % json.dumps(q, ensure_ascii=False)[:200])
    return 0


def gate_check(port=9222, need=1, label=""):
    """★★ 闲时额度门禁 —— 提交**之前**必须过这一关。

    返回 (ok, info)：
      ok=True  ⇒ 还能跑（info 带 used/total/left/need）
      ok=False ⇒ **额度不足，调用方应当停止**并提示用户（换账号 / 等明天）

    ★ 数据源 = `GET /vidu/v1/tasks/quota` → `nopeak_quota:{used,total}`
      —— 就是页面左下角「用量 used/total」那个数（2026-10-13 实测确认）。
      ⛔ 不要用 /credit/v1/credits/me 猜额度（那是积分，口径完全不同）。
    """
    d = call({"op": "quota"}, port=port, timeout=90)
    nq = d.get("nopeak_quota")
    if not nq:
        return True, {"unknown": True, "reason": str(d.get("quota_raw"))[:200]}
    used = int(nq.get("used") or 0)
    total = int(nq.get("total") or 0)
    left = max(0, total - used)
    return (left >= need), {"used": used, "total": total, "left": left, "need": need,
                            "label": label}


def _quota_stop_msg(info, need):
    """★★ 额度不足时的**统一停止提示** —— 必须让用户一眼知道：停下了 + 下一步做什么。

    ★ 关键：额度当天用完后**不会自己恢复**（0 点才重置）⇒ 只有"换账号 / 等明天"两条路。
      所以这里**不等待、不重试**，直接停下并说清楚。
    """
    sd = os.path.dirname(os.path.abspath(__file__))
    py = os.path.basename(sys.executable)
    bar = "=" * 66
    return "\n".join([
        "", bar,
        " ⛔ 闲时额度不足 —— 已停止作业（不硬跑到平台报错，也不空转等待）",
        bar,
        " 今日闲时：%s / %s（剩余 %s）" % (info.get("used"), info.get("total"), info.get("left")),
        " 本次需要：%s" % need,
        "",
        " ⇒ 二选一：",
        "    A. 【换账号】今天继续跑",
        "       %s \"%s\\vidu_edge.py\" up --account 账号2" % (py, sd),
        "       然后重跑本命令 —— 已完成的组会自动跳过",
        "    B. 【等明天】额度 0 点重置",
        "       明早重跑同一条命令即可，接着跑剩下的",
        "",
        " ⚠ 别反复重跑：额度没恢复时，重跑会立刻再次停在这里（不影响已完成部分）。",
        bar, "",
    ])


def cmd_elements(a):
    d = call({"op": "elements"}, port=a.port, timeout=90)
    for e in d.get("elements") or []:
        print("%-20s %-18s v=%-12s %s" % (e["name"][:18], e["id"], e["version"],
                                          ",".join(e.get("labels") or [])))
    return 0


def _load_jobs(path, a):
    d = json.load(open(path, encoding="utf-8"))
    jobs = d["jobs"]
    if a.job:
        jobs = [j for j in jobs if j["id"] == a.job]
    if a.limit:
        jobs = jobs[:a.limit]
    return jobs


def cmd_run(a):
    jobs = _load_jobs(a.jobs, a)
    if not jobs:
        print("★ 没有匹配的组"); return 1
    print("计划直提 %d 组，mode=%s" % (len(jobs), a.mode))
    # ★★ 闲时额度门禁 —— 提交**之前**先看够不够，别跑到一半被平台拒
    if a.mode == "nopeak" and not a.dry_run:
        okq, info = gate_check(a.port, need=len(jobs), label="本次批量")
        if info.get("unknown"):
            print("  ⚠ 额度读不到（%s）—— 继续提交，由平台错误兜底" % info.get("reason"))
        elif not okq:
            print(_quota_stop_msg(info, len(jobs)))
            return 2
        else:
            print("  额度：闲时 %s/%s，剩余 %s ≥ 需要 %s ⇒ 放行"
                  % (info["used"], info["total"], info["left"], len(jobs)))
    ok = fail = 0
    for i, j in enumerate(jobs, 1):
        text = render_prompt(j["prompt"])
        imgs = [img_spec(x["path"], x.get("name")) for x in j.get("images", [])]
        aud_list = j.get("audios") or []
        auds = [audio_spec(x["path"], x.get("speaker")) for x in aud_list]
        missing = [x["path"] for x in j.get("images", []) if not os.path.exists(x["path"])] \
            + [x["path"] for x in aud_list if not os.path.exists(x["path"])]
        if missing:
            print("[%d/%d] %s ✗ 缺文件 %d 个: %s" % (i, len(jobs), j["id"], len(missing), missing[:3]))
            fail += 1
            continue
        if len(auds) > 3:
            print("   ⚠ 音色 %d 个 > 平台上限 3，只取前 3" % len(auds))
            auds = auds[:3]
        spec = {"op": "submit", "text": text, "images": imgs, "audios": auds, "subjects": [],
                "settings": build_settings(j.get("duration", 5), j.get("ratio", "9:16"), a.mode)}
        if a.dry_run:
            print("[%d/%d] %s  图%d 音%d  时长%s  ratio=%s  mode=%s\n     text[:80]=%s"
                  % (i, len(jobs), j["id"], len(imgs), len(auds), j.get("duration"),
                     j.get("ratio"), a.mode, text[:80].replace("\n", " ")))
            continue
        t0 = time.time()
        d = call(spec, port=a.port)
        if d.get("status") == 200:
            ok += 1
            print("[%d/%d] %s ✓ task=%s  (%.1fs)" % (i, len(jobs), j["id"], d.get("task_id"),
                                                     time.time() - t0))
            log({"mode": "api_submit", "job": j["id"], "task": d.get("task_id"), "sched": a.mode})
        else:
            fail += 1
            print("[%d/%d] %s ✗ %s" % (i, len(jobs), j["id"], d.get("error") or d.get("status")))
            log({"mode": "api_submit", "job": j["id"], "error": str(d.get("error"))[:300]})
        time.sleep(a.pause)
    print("\n直提 %d ｜ 失败 %d" % (ok, fail))
    return 0 if not fail else 1


def cmd_raw(a):
    """★ **仅上传模式** —— 用户自带提示词（**不一定是我们格式**），
    **一个字都不改**，只帮他上传资产并按顺序挂上去。

    用途：客户已有自己习惯的提示词，只想让工具替他做"上传 + 分配资产 + 提交"。

    资产分配规则（**不改文本**，只决定第几张图/第几个音频挂上去）：
      · `--image a.png --image b.png …` 按顺序 = `图1`/`图2`/…
      · `--audio x.wav …` 按顺序 = `音频1`/`音频2`/…
      · 与用户提示词里写的 `[@图N]` / `[@音频N]` 对应关系**由用户保证**（我们不动文本）。
    ⚠ 会**检查**文本里是否出现 `[@图` 之类引用标记，没有就**告警**（但仍照提交）。
    """
    if not os.path.exists(a.prompt_file):
        print("★ 提示词文件不存在: %s" % a.prompt_file)
        return 1
    with open(a.prompt_file, encoding="utf-8") as f:
        text = f.read()
    imgs = [img_spec(p) for p in (a.image or [])]
    auds = [audio_spec(p) for p in (a.audio or [])][:3]
    miss = [x for x in (a.image or []) if not os.path.exists(x)] \
        + [x for x in (a.audio or []) if not os.path.exists(x)]
    if miss:
        print("★ 缺文件: %s" % miss); return 1

    # 只做"提示"，不改文本
    marks = [m for m in ("[@图", "[@音频", "pn1,", "vm1,", "@图片", "@音频")
             if m in text]
    if imgs and "[@图" not in text and "pn" not in text and "@图片" not in text:
        print("⚠ 提示词里没看到 `[@图N]` 这类图片引用标记 —— 资产仍会挂上，"
              "但模型可能用不到。请确认你的提示词里写了引用。")
    print("提示词 %d 字 ｜ 检测到的引用标记: %s ｜ 图%d 音%d"
          % (len(text), (marks or "（无）"), len(imgs), len(auds)))

    spec = {"op": "submit", "text": text, "images": imgs, "audios": auds, "subjects": [],
            "settings": build_settings(a.duration, a.ratio, a.mode, a.resolution)}
    if a.dry_run:
        print("[dry-run] 不改文本，仅上传 %d 图 / %d 音，然后提交（mode=%s）"
              % (len(imgs), len(auds), a.mode))
        print("    文本开头: %s" % text[:100].replace("\n", " "))
        return 0
    d = call(spec, port=a.port)
    if d.get("status") == 200:
        print("★ 已提交 task=%s" % d.get("task_id"))
        log({"mode": "raw_submit", "task": d.get("task_id"), "n_img": len(imgs),
             "n_aud": len(auds), "sched": a.mode})
        return 0
    print("✗ 提交失败:", d.get("error") or d.get("status"))
    log({"mode": "raw_submit", "error": str(d.get("error"))[:300]})
    return 1


def main():
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from license import ensure_activated
    if not ensure_activated():
        return 3
    ap = argparse.ArgumentParser(description="Vidu 直连提交（不点 UI）")
    ap.add_argument("--port", type=int, default=9222)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("quota").set_defaults(fn=cmd_quota)
    sub.add_parser("elements").set_defaults(fn=cmd_elements)

    p = sub.add_parser("run")
    p.add_argument("--jobs", required=True)
    p.add_argument("--job", default="")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--mode", choices=["nopeak", "normal"], default="nopeak")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--pause", type=float, default=1.0)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("raw", help="★ 仅上传模式：用户自带提示词，一个字不改，只帮挂资产并提交")
    p.add_argument("--prompt-file", required=True, help="用户自己的提示词文件（原样使用）")
    p.add_argument("--image", action="append", default=[], help="按顺序 = 图1/图2/…（可重复）")
    p.add_argument("--audio", action="append", default=[], help="按顺序 = 音频1/音频2/…（可重复，≤3）")
    p.add_argument("--duration", type=int, default=5)
    p.add_argument("--ratio", default="9:16")
    p.add_argument("--resolution", default="1080p")
    p.add_argument("--mode", choices=["nopeak", "normal"], default="nopeak")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_raw)

    a = ap.parse_args()
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
