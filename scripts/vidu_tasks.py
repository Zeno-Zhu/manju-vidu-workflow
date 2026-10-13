# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:3d43e8b4f6 · 溯源标记，请保留
"""vidu_tasks.py —— Vidu 任务台账 / 无水印下载 / 重命名（★ 渠道第二入口）。

═══ 它解决什么 ═══
提交完只拿到"提交成功"，**不知道哪条成片对应哪个分镜组**。本模块把
「平台任务 id」与「分镜组 id」对上，再把**无水印**成片按项目规范落盘。

═══ 三个子命令 ═══
  list      拉任务历史（自动翻页）→ 写台账 ~/.manju-vidu/tasks.json
  match     台账 ⟷ jobs_vidu.json 对齐 → 打印「组id → 选中 task_id」映射（不落盘）
  download  下载选中任务的**无水印**成片 → 重命名落盘

═══ 用法 ═══
  py vidu_tasks.py list
  py vidu_tasks.py match    --jobs <jobs_vidu.json>
  py vidu_tasks.py download --jobs <jobs_vidu.json> [--groups 1_1_标准] [--dry-run] [--force]

  ★★ 同一组分多轮提交时（旧稿一轮 + 重写稿一轮）**必须带 --since** 隔离：
     py vidu_tasks.py download --jobs <jobs_vidu.json> --since 2026-10-07T09 --force
     （不加：新稿还没跑完的组会**回退下到旧稿成片**。实测两轮落在不同小时。）

  ⚠ 必须用**系统 Python 3.14**（CDP 依赖 websocket-client）。

═══ ★★ 实测硬事实（2026-10-07）═══
  列表: GET https://service.vidu.cn/vidu/v1/tasks/history/me?page_size=100
        → {total(恒0), tasks:[...], next_page_token:{created_at:"…Z"}}
        翻页: 再带 `created_at=<上页 next_page_token.created_at>`
  任务对象关键字段:
    id / state / created_at / err_code / err_msg / settings{duration,aspect_ratio,resolution}
    input.prompts[0].content  ← **提交的提示词原文**，内含唯一标记 `分镜组ID：X`
    creations[0]:
      .uri          = …/segment-00/**watermarked**.mp4   （有水印）
      .download_uri = …/segment-00/**video.mp4**          （★ 无水印，带 auth_key 可裸下）
      .nomark_uri   = 同 download_uri
      .cover_uri    = 封面 jpeg
      .video{duration,fps,resolution{width,height},ratio}
      .has_audio
  state 词: created → preparation → processing → **success / failed**
  ⇒ 组↔任务 的锚点 = 提示词里的 `分镜组ID：<组id>`（不靠顺序、不靠时间，稳）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vidu_cdp import CDP            # noqa: E402
from vidu_log import STATE_DIR, log  # noqa: E402

LIST_API = "https://service.vidu.cn/vidu/v1/tasks/history/me"
RE_GROUP = re.compile(r"分镜组ID：([^\n\r]+)")
OK_STATES = ("success",)

# ---------- 拉列表（在页面上下文里 fetch，复用登录态；自动翻页） ----------
# ★★ 2026-10-13 修正翻页参数（旧写法用 created_at 游标，实测**只能拿到第 1 页**）：
#   实测两套分页：
#     · `page_size=100`          → 100 条 + `next_page_token.created_at`（游标式）
#     · `pager.pagesz=N&pager.page=M` → **页码式**，★ 只写 `pager.page=M` 时每页只有 **10** 条
#   本项目用页码式：`pager.pagesz=100&pager.page=M`（实测 page=2 返回的是另一批 100 条）。
#   ⚠ 老剧的组会被新任务挤出第 1 页 ⇒ 不翻页会**整批报「台账里没有该组」**（踩过）。
JS_FETCH = r"""(async () => {
  const out = [];
  for (let page = 1; page <= %d; page++) {
    let j;
    try {
      const r = await fetch('%s?pager.pagesz=%d&pager.page=' + page,
                            {method: 'GET', credentials: 'include'});
      j = await r.json();
    } catch (e) { return {err: String(e), got: out.length}; }
    const ts = j.tasks || [];
    if (!ts.length) break;
    for (const t of ts) {
      const txt = (((t.input || {}).prompts || [])[0] || {}).content || '';
      const m = txt.match(/分镜组ID：([^\n\r]+)/);
      const ms = txt.match(/(?:^|\n)(△[^\n\r]*)/);
      const cr = (t.creations || [])[0] || null;
      out.push({
        id: t.id, state: t.state, created_at: t.created_at,
        group: m ? m[1].trim() : null,
        psig: ms ? ms[1].trim().slice(0, 60) : '',
        plen: txt.length,
        n_creations: (t.creations || []).length,
        err_code: t.err_code || '', err_msg: t.err_msg || '',
        settings: {
          duration: (t.settings || {}).duration,
          ratio: (t.settings || {}).aspect_ratio,
          resolution: (t.settings || {}).resolution,
          codec: (t.settings || {}).codec,
          schedule_mode: (t.settings || {}).schedule_mode
        },
        creation: cr ? {
          id: cr.id, uri: cr.uri || '', download_uri: cr.download_uri || '',
          nomark_uri: cr.nomark_uri || '', cover_uri: cr.cover_uri || '',
          has_audio: !!cr.has_audio, video: cr.video || null
        } : null
      });
    }
    // 页码式：本页不满 ⇒ 已是最后一页
    if (ts.length < %d) break;
  }
  return {tasks: out};
})()"""


def fetch_tasks(port: int = 9222, max_pages: int = 20, page_size: int = 100):
    js = JS_FETCH % (max_pages, LIST_API, page_size, page_size)
    with CDP(port=port, timeout=40) as c:
        try:
            c.send("Page.bringToFront")          # ★ 后台标签会冻结 fetch 的 promise
            c.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception:
            pass
        d = c.eval(js, timeout=120)
    if isinstance(d, dict) and d.get("err"):
        raise RuntimeError("拉列表失败: %s" % d["err"])
    return (d or {}).get("tasks") or []


def save_ledger(tasks, proj=None):
    os.makedirs(STATE_DIR, exist_ok=True)
    rec = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "count": len(tasks), "tasks": tasks}
    p = os.path.join(STATE_DIR, "tasks.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    if proj:
        d = os.path.join(proj, "_出片记录")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "_vidu_tasks.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
    return p


def load_ledger():
    p = os.path.join(STATE_DIR, "tasks.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ---------- 台账 ⟷ 作业单 对齐 ----------
def _has_url(t):
    """★ 只算**无水印**地址 —— 否则会把「只有水印版」的中间态当成可下载（实测 2026-10-13）。"""
    c = (t or {}).get("creation") or {}
    return bool(c.get("download_uri") or c.get("nomark_uri"))


def _sig_of_prompt(text, n=60):
    """取提示词里第一条 `△…` 行当「稿本签名」。用于区分**同一组的旧稿 / 新稿**任务。"""
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("△"):
            return s[:n]
    return ""


def pick_for_group(tasks, group, since=None):
    """一组可能提交过多次 ⇒ 取**最新的 success**；没有 success 再退最新的一条。
    返回 (task, 说明)。

    ★ 2026-10-07 补：实测存在「state=success 但 creations[].download_uri 还是空」的
      **中间态**（平台先置 success、稍后才挂地址）。所以 success 里**优先取带地址的**，
      排序键 = (有没有地址, 创建时间) 双降序；这样只要同组有任一可用任务就不会误报"无成片地址"。

    ★★ since（老稿 / 新稿隔离，2026-10-07 用户实跑暴露）：
      同一分镜组 id 会**被提交多次**（旧稿一轮 + 重写稿一轮），`分镜组ID：X` 标记两边都有 ⇒
      不隔离的话，「新稿还没跑完」的组会**回退到旧稿 success 被当成新稿下载**。
      实测两轮提交落在**不同小时**（旧稿 07:xx、新稿 09:xx UTC），且 53 组完全对齐、互不重叠。
      ⇒ 传 `since="2026-10-07T09"`（ISO 前缀，字符串比较即按时间）只认这一轮及之后的任务。
      ⛔ 别用「有没有机位/特写字样」当指纹 —— 53 组新稿全都含「机位」，旧稿也有，区分不开。
    """
    mine = [t for t in tasks if t.get("group") == group]
    if since:
        mine = [t for t in mine if (t.get("created_at") or "") >= since]
    if not mine:
        return None, ("自 %s 起台账里没有该组（旧稿被隔离）" % since) if since else "台账里没有该组"
    suc = [t for t in mine if t.get("state") in OK_STATES]
    pool = suc or mine
    pool = sorted(pool, key=lambda t: (_has_url(t), t.get("created_at") or ""), reverse=True)
    t = pool[0]
    if suc:
        note = "success×%d/%d" % (len(suc), len(mine)) if len(mine) > 1 else "success"
        if not _has_url(t):
            note += "（暂无可下载地址，稍后重跑 list）"
        return t, note
    return t, "无 success（最新=%s）" % t.get("state")


def build_plan(tasks, jobs, since=None):
    """返回 [{group, task_id, state, note, dest, url, dur, res}] —— 不含写盘。"""
    plan = []
    for j in jobs:
        g = j["id"]
        t, note = pick_for_group(tasks, g, since=since)
        row = {"group": g, "note": note, "task": t}
        plan.append(row)
    return plan


def dest_path(out_root, group, ep_dir="EP01"):
    """落盘路径。

    ★ 用户 2026-10-07 定案：「直接把所有视频全部下载到 EP01，和云鲸的错开就行」
      ⇒ 全部落到 `<项目>/02_分集工程/EP01/`（**不再按组号前缀分集**），
        文件名带 `_vidu` 后缀 —— 云鲸的同名成片躺在 `02_分集工程/` **根下**
        （如 `1_1_标准.mp4`），靠后缀与目录双重错开。
    """
    return os.path.join(out_root, ep_dir, "%s_vidu.mp4" % group)


# ---------- 下载（auth_key 签名地址，裸 HTTP 即可，无需 cookie） ----------
def http_download(url, dest, tries=3, timeout=180):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
                n = 0
                while True:
                    b = r.read(1 << 16)
                    if not b:
                        break
                    f.write(b)
                    n += len(b)
            if n < 1024:
                raise RuntimeError("下载过小 %d 字节" % n)
            os.replace(part, dest)
            return n
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("下载失败: %s" % last)


def resolve_url(t, allow_watermark=False):
    """★ 只返回**无水印**地址。返回 (url, kind)：kind = 'clean' | 'watermarked' | None(空)。

    ⚠ 实测（2026-10-13，图片与视频同规律）：
      `uri` = `watermarked.*`（带「Vidu AI 生成」水印）；
      `download_uri` / `nomark_uri` = `video.mp4` / `image.png`（**无水印**）。
      ⛔ 平台**先置 success、稍后才挂无水印文件** ⇒ 这时 `download_uri` 可能为空，
      旧代码会退到 `uri` **静默下到带水印版**。现在**不再静默退**：
      默认返回 `kind='watermarked'` 让调用方**跳过并告警**；只有显式允许才用。
    """
    c = (t or {}).get("creation") or {}
    for k in ("download_uri", "nomark_uri"):
        if c.get(k):
            return c[k], "clean"
    if c.get("uri"):
        return (c["uri"], "watermarked") if allow_watermark else (None, "watermarked")
    return None, None


def cmd_list(a):
    tasks = fetch_tasks(a.port, max_pages=a.max_pages)
    proj = os.path.dirname(os.path.dirname(os.path.abspath(a.jobs))) if a.jobs else None
    p = save_ledger(tasks, proj)
    print("拉到 %d 条任务 → 台账 %s" % (len(tasks), p))
    cnt = {}
    for t in tasks:
        cnt[t.get("state")] = cnt.get(t.get("state"), 0) + 1
    print("状态分布:", json.dumps(cnt, ensure_ascii=False))
    marked = [t for t in tasks if t.get("group")]
    print("带「分镜组ID」标记的:", len(marked))
    print("起草本签名(psig)的:", len([t for t in tasks if t.get("psig")]))
    if a.jobs:
        jobs = json.load(open(a.jobs, encoding="utf-8"))["jobs"]
        gids = {j["id"] for j in jobs}
        seen = {}
        for t in tasks:
            if t.get("group") in gids:
                k = (t.get("created_at") or "")[:13]
                seen[k] = seen.get(k, 0) + 1
        print("按小时×本剧组:", json.dumps(seen, ensure_ascii=False),
              "（★ 新旧稿分轮的指纹；download 用 --since 隔离）")
    return 0


def cmd_match(a):
    led = load_ledger()
    if not led:
        print("★ 先跑 `list` 生成台账"); return 1
    jobs = json.load(open(a.jobs, encoding="utf-8"))["jobs"]
    tasks = led["tasks"]
    since = a.since or None
    print("台账 %s（%d 条）%s" % (led.get("fetched_at"), led["count"],
                                 ("｜since=%s" % since) if since else ""))
    print("%-12s | %-18s | %-9s | %s" % ("组", "task_id", "state", "备注"))
    print("-" * 70)
    n_ok = 0
    for j in jobs:
        t, note = pick_for_group(tasks, j["id"], since=since)
        if t is None:
            print("%-12s | %-18s | %-9s | %s" % (j["id"], "-", "-", note)); continue
        if t.get("state") in OK_STATES:
            n_ok += 1
        print("%-12s | %-18s | %-9s | %s" % (j["id"], t["id"], t.get("state"), note))
    print("-" * 70)
    print("可下载（success）%d / %d 组" % (n_ok, len(jobs)))
    return 0


def cmd_download(a):
    led = load_ledger()
    if not led:
        print("★ 先跑 `list` 生成台账"); return 1
    tasks = led["tasks"]
    jobs = json.load(open(a.jobs, encoding="utf-8"))["jobs"] if a.jobs else []
    since = a.since or None
    if a.groups:
        want = {s.strip() for s in a.groups.split(",") if s.strip()}
        jobs = [j for j in jobs if j["id"] in want]
    proj = os.path.dirname(os.path.dirname(os.path.abspath(a.jobs)))
    out_root = a.out_root or os.path.join(proj, "02_分集工程")
    ep_dir = a.ep_dir

    done, skip, fail = [], [], []
    for j in jobs:
        g = j["id"]
        t, note = pick_for_group(tasks, g, since=since)
        if t is None:
            if since:
                print("%-12s · 跳过（%s）" % (g, note)); skip.append(g); continue
            print("%-12s ✗ %s" % (g, note)); fail.append((g, note)); continue
        if t.get("state") not in OK_STATES:
            print("%-12s · 跳过（%s，%s）" % (g, t.get("state"), note)); skip.append(g); continue
        url, which = resolve_url(t, allow_watermark=a.allow_watermark)
        if which == "watermarked" and not url:
            # ★ 平台「先置 success、稍后才挂无水印文件」的中间态 —— 跳过而非将就下水印版
            print("%-12s ⚠ 只有水印版地址（中间态）—— 跳过，稍后重跑 list+download" % g)
            skip.append(g); continue
        if not url:
            print("%-12s ✗ 任务无成片地址" % g); fail.append((g, "无成片地址")); continue
        dest = dest_path(out_root, g, ep_dir)
        if os.path.exists(dest) and not a.force:
            print("%-12s · 已存在，跳过（--force 覆盖）：%s" % (g, dest)); skip.append(g); continue
        v = (t.get("creation") or {}).get("video") or {}
        res = v.get("resolution") or {}
        tag = "%ss %sx%s %s" % (t.get("settings", {}).get("duration") or v.get("duration"),
                                res.get("width"), res.get("height"), which)
        if a.dry_run:
            print("%-12s → %s   [%s]" % (g, dest, tag)); done.append(g); continue
        try:
            n = http_download(url, dest)
            print("%-12s ✓ %s  (%.1f MB, %s)" % (g, dest, n / 1048576.0, tag))
            cov = (t.get("creation") or {}).get("cover_uri")
            if cov:
                try:
                    http_download(cov, dest.replace(".mp4", "_cover.jpg"))
                except Exception:
                    pass
            done.append(g)
        except Exception as e:
            print("%-12s ✗ %s" % (g, e)); fail.append((g, str(e)[:120]))
    print("\n下载 %d｜跳过 %d｜失败 %d" % (len(done), len(skip), len(fail)))
    if fail:
        print("失败清单:", json.dumps(fail, ensure_ascii=False))
    log({"job": "*", "mode": "download", "ok": done, "skip": skip,
         "fail": [f[0] for f in fail], "out_root": out_root, "dry": bool(a.dry_run),
         "since": since})
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("list", "match", "download"):
        p = sub.add_parser(name)
        p.add_argument("--jobs", default="", help="jobs_vidu.json（用于定项目根 / 组清单）")
        p.add_argument("--port", type=int, default=9222)
        p.add_argument("--max-pages", type=int, default=6)
        p.add_argument("--since", default="",
                       help="ISO 时间前缀（如 2026-10-07T09）；只认该时刻及之后提交的任务。"
                            "★ 同一组被提交过多轮（旧稿/新稿）时**必传**，否则会下到旧稿成片")
        if name == "download":
            p.add_argument("--groups", default="", help="逗号分隔，只下这些组")
            p.add_argument("--out-root", default="", help="默认 <项目>/02_分集工程")
            p.add_argument("--ep-dir", default="EP01",
                           help="落盘子目录（默认 EP01：用户 2026-10-07 要求全部下到 EP01）")
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--force", action="store_true")
            p.add_argument("--allow-watermark", action="store_true",
                           help="★ 默认**只下无水印**；开启后才允许在等不到无水印时退到带水印版")
    a = ap.parse_args()
    if a.cmd == "list":
        return cmd_list(a)
    if a.cmd == "match":
        return cmd_match(a)
    return cmd_download(a)


if __name__ == "__main__":
    sys.exit(main())
