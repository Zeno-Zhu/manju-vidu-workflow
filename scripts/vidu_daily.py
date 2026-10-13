# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:40089c01a9 · 溯源标记，请保留
"""vidu_daily.py —— Vidu 每日收尾：拉台账 → 下载 → 按「剧名 / 分集 / 分镜组」重命名 → 出日报（★ 任务 3）。

═══ 它解决什么 ═══
每天闲时批次跑完，平台上堆一堆**无名任务**（`3506…` 这种 id）。
本模块把「平台任务」翻译回「剧名 + 分镜组」，**批量重命名落盘**，并给一份当天可回顾的日报。

═══ 落盘规则（★ 用户 2026-10-13 定）═══
  <项目>/02_分集工程/<EPxx>/<组id>_vidu.mp4           ← 剧名 = 项目目录名（已满足「按剧名建文件夹」）
  --by-ep 时 EPxx 由组 id 前缀自动推断：`1_1_标准` → EP01
  默认全部落 EP01（用户 2026-10-07 定，与云鲸错开靠 `_vidu` 后缀 + 子目录）

═══ 用法 ═══
  PY="C:/Users/Administrator/AppData/Local/Microsoft/WindowsApps/python.exe"
  S="C:/Users/Administrator/.workbuddy/skills/manju-vidu/scripts"
  J="<项目>/_vidu渠道/jobs_vidu.json"

  $PY $S/vidu_daily.py report  --jobs "$J"                       # 只看不下载
  $PY $S/vidu_daily.py collect --jobs "$J" --dry-run             # 看落盘位置
  $PY $S/vidu_daily.py collect --jobs "$J" --by-ep               # 下载（按集分目录）
  $PY $S/vidu_daily.py collect --jobs "$J" --since 2026-10-13T07 # 只收今天这批

⚠ 系统 Python 3.14 + **沙箱外**。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vidu_tasks import (fetch_tasks, pick_for_group, resolve_url,  # noqa: E402
                        http_download, OK_STATES)
from vidu_log import log   # noqa: E402

RE_EP = re.compile(r"^(\d+)[_\-]")


def ep_of(group, default="EP01"):
    """组 id → 分集目录。`1_1_标准` → EP01；推不出就用 default。"""
    m = RE_EP.match(group or "")
    if not m:
        return default
    try:
        return "EP%02d" % int(m.group(1))
    except Exception:
        return default


def collect(args):
    jobs = json.load(open(args.jobs, encoding="utf-8"))["jobs"]
    if args.groups:
        want = {s.strip() for s in args.groups.split(",") if s.strip()}
        jobs = [j for j in jobs if j["id"] in want]
    proj = os.path.dirname(os.path.dirname(os.path.abspath(args.jobs)))
    out_root = args.out_root or os.path.join(proj, "02_分集工程")

    print("拉台账…")
    tasks = fetch_tasks(args.port, max_pages=args.max_pages)
    print("台账 %d 条%s" % (len(tasks), ("（since=%s）" % args.since) if args.since else ""))

    plan, n_ok, n_wait, n_miss = [], 0, 0, 0
    for j in jobs:
        t, note = pick_for_group(tasks, j["id"], since=args.since or None)
        st = (t or {}).get("state")
        row = {"group": j["id"], "task": (t or {}).get("id"), "state": st, "note": note,
               "ep": ep_of(j["id"], args.ep_dir) if args.by_ep else args.ep_dir}
        url, which = resolve_url(t, allow_watermark=getattr(args, "allow_watermark", False)) if t else (None, None)
        row["url"] = url
        row["which"] = which
        if st in OK_STATES and url:
            n_ok += 1
        elif which == "watermarked":
            n_wait += 1          # ★ 只有水印版 ⇒ 算"还没就绪"，不是失败
        elif st is None:
            n_miss += 1
        else:
            n_wait += 1
        plan.append(row)

    print("可下载（无水印）%d ｜ 未完成/排队/待挂文件 %d ｜ 台账无记录 %d\n" % (n_ok, n_wait, n_miss))
    done, skip, fail = [], [], []
    for r in plan:
        g = r["group"]
        dest = os.path.join(out_root, r["ep"], "%s_vidu.mp4" % g)
        if not r["url"]:
            if r.get("which") == "watermarked":
                print("  · %-16s 跳过（只有水印版地址＝中间态，稍后重跑）" % g)
            else:
                print("  · %-16s 跳过（%s）" % (g, r["state"] or "无任务"))
            skip.append(g)
            continue
        if os.path.exists(dest) and not args.force:
            print("  · %-16s 已存在（--force 覆盖）" % g)
            skip.append(g)
            continue
        if args.dry_run:
            print("  → %-16s %s" % (g, dest))
            done.append(g)
            continue
        try:
            n = http_download(r["url"], dest)
            print("  ✓ %-16s %s  (%.1f MB)" % (g, dest, n / 1048576.0))
            done.append(g)
        except Exception as e:
            print("  ✗ %-16s %s" % (g, str(e)[:100]))
            fail.append({"group": g, "err": str(e)[:160]})
    return plan, done, skip, fail, out_root


def write_report(proj, plan, done, skip, fail, out_root, dry):
    rec = {"at": datetime.now().isoformat(timespec="seconds"), "date": datetime.now().strftime("%Y-%m-%d"),
           "out_root": out_root, "dry_run": bool(dry),
           "counts": {"total": len(plan), "downloaded": len(done), "skipped": len(skip),
                      "failed": len(fail)},
           "downloaded": done, "failed": fail,
           "pending": [r["group"] for r in plan if not r.get("url")]}
    d = os.path.join(proj or out_root, "_出片记录")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "_vidu_daily_report.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    lines = ["# Vidu 每日收尾日报 %s" % rec["date"], "",
             "- 本批组数：**%d**" % rec["counts"]["total"],
             "- 已下载：**%d**" % rec["counts"]["downloaded"],
             "- 跳过（已存在/未完成）：**%d**" % rec["counts"]["skipped"],
             "- 失败：**%d**" % rec["counts"]["failed"], ""]
    if rec["pending"]:
        lines += ["## 还没成片的组（不是失败，别重提交）", ""] + \
                 ["- %s" % g for g in rec["pending"]] + [""]
    if fail:
        lines += ["## 失败", ""] + ["- %s：%s" % (f["group"], f["err"]) for f in fail] + [""]
    md = os.path.join(d, "_vidu_daily_report.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return p, md


def main():
    ap = argparse.ArgumentParser(description="Vidu 每日收尾：下载 + 重命名 + 日报")
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--max-pages", type=int, default=30,
                    help="台账翻页上限（每页 100；老剧任务会被新任务挤出第 1 页）")
    ap.add_argument("--since", default="", help="ISO 前缀（如 2026-10-13T07）；只收这批及之后")
    ap.add_argument("--groups", default="")
    ap.add_argument("--out-root", default="")
    ap.add_argument("--ep-dir", default="EP01")
    ap.add_argument("--by-ep", action="store_true", help="按组 id 前缀自动分 EP01/EP02…")
    ap.add_argument("--dry-run", action="store_true",
                    help="只看落盘位置 / 出日报，不真下载（= report 模式）")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--allow-watermark", action="store_true",
                    help="★ 默认**只下无水印**；开启后才允许退到带水印版")
    a = ap.parse_args()

    proj = os.path.dirname(os.path.dirname(os.path.abspath(a.jobs)))
    plan, done, skip, fail, out_root = collect(a)
    p, md = write_report(proj, plan, done, skip, fail, out_root, a.dry_run)
    log({"mode": "daily_collect", "done": len(done), "skip": len(skip), "fail": len(fail),
         "dry": bool(a.dry_run)})
    print("\n日报 → %s\n       %s" % (p, md))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
