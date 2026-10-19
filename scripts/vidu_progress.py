# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:4fdcb8a2ba · 溯源标记，请保留
"""vidu_progress.py —— Vidu 出片进度一键盘点（**多账号合并**）。

═══ 它解决什么 ═══
`vidu_tasks.py list` 每跑一个端口就把台账**覆盖**写进 `~/.manju-vidu/tasks.json`
⇒ 五个账号跑完只剩最后一个的台账，"全局还剩多少没生成"根本看不出来。
本工具按端口**逐个采集后合并**，再把结果对齐到本项目作业单，一次性回答：

    已出片 / 仍在队列 / 失败 / 台账里查不到   各多少组，分别是谁。

═══ 用法 ═══
  python vidu_progress.py --jobs <jobs_vidu.json>
  python vidu_progress.py --jobs <jobs_vidu.json> --ports 9222,9223      # 只查部分实例
  python vidu_progress.py --jobs <jobs_vidu.json> --json                 # 输出 JSON 明细

⚠ 必须用**系统 Python 3.14**、**沙箱外**跑；浏览器实例要先起（`vidu_edge.py up`）。
★ 失败组会单独列出 `err_code` —— 若是 `CreationPolicyViolation`，
  说明是**内容审核**不过，走"合规改写"（改画面描述：去可辨认文字 / 去执法语义 /
  去人身接触 / 去激烈定性），⛔ 不要原样重投。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vidu_tasks import fetch_tasks  # noqa: E402

DEFAULT_PORTS = [9222, 9223, 9224, 9225, 9226]
STATE_DIR = os.path.join(os.path.expanduser("~"), ".manju-vidu")


def has_url(t):
    """★ 只认**无水印**地址（`download_uri` / `nomark_uri`）。"""
    c = (t or {}).get("creation") or {}
    return bool(c.get("download_uri") or c.get("nomark_uri"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True, help="作业单 jobs_vidu*.json（决定本项目的组清单）")
    ap.add_argument("--ports", default="", help="逗号分隔，默认 9222..9226")
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--json", action="store_true", help="额外打印 JSON 明细")
    a = ap.parse_args()

    ports = ([int(x) for x in a.ports.split(",") if x.strip()] if a.ports else DEFAULT_PORTS)
    jobs = json.load(open(a.jobs, encoding="utf-8"))["jobs"]
    ids = [j["id"] for j in jobs]

    merged, per_port = {}, {}
    for p in ports:
        try:
            ts = fetch_tasks(p, max_pages=a.max_pages)
        except Exception as e:  # noqa: BLE001
            print("端口 %d 拉取失败：%s" % (p, str(e)[:120]))
            per_port[p] = 0
            continue
        per_port[p] = len(ts)
        for t in ts:
            k = t.get("id") or json.dumps(t, ensure_ascii=False)[:80]
            merged[k] = t
        st = {}
        for t in ts:
            st[t.get("state")] = st.get(t.get("state"), 0) + 1
        print("端口 %d：%3d 条 ｜ %s" % (p, len(ts), json.dumps(st, ensure_ascii=False)))

    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "tasks_all.json"), "w", encoding="utf-8") as f:
        json.dump({"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "count": len(merged), "tasks": list(merged.values())},
                  f, ensure_ascii=False, indent=1)

    by_group = {}
    for t in merged.values():
        g = t.get("group")
        if g:
            by_group.setdefault(g, []).append(t)

    ok, queued, failed, missing = [], [], [], []
    for g in ids:
        ts = by_group.get(g)
        if not ts:
            missing.append(g)
            continue
        ts.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        # ★★ 2026-10-15 修：**先找"最新的那个带无水印地址的 success"**，找到就算已出片。
        #   不能直接取最新一条判状态 —— 同一组可能被提交过多轮（重投/合规改写），
        #   最新那条可能是 failed，但**前面已经有一条 success 且能下载**（实测 1_2_标准 就是
        #   两条 success + 一条 failed，旧写法把它误报成"失败"，白重投一轮）。
        wins = [t for t in ts if t.get("state") == "success" and has_url(t)]
        if wins:
            ok.append(g)
            continue
        t = ts[0]
        st = t.get("state")
        if st == "failed":
            failed.append((g, t.get("err_code") or "", t.get("created_at") or ""))
        else:
            queued.append((g, st))

    total = len(ids)
    print("\n================= 本剧出片进度（共 %d 组）=================" % total)
    print("✅ 已出片（success ＋ 无水印地址）: %d" % len(ok))
    print("⏳ 仍在队列/生成中               : %d" % len(queued))
    print("❌ 失败                          : %d" % len(failed))
    print("⚠️  台账里查不到（未对上）        : %d" % len(missing))
    if queued:
        d = {}
        for _, s in queued:
            d[s] = d.get(s, 0) + 1
        print("   队列按状态:", json.dumps(d, ensure_ascii=False))
    if failed:
        d = {}
        for _, c, _ts in failed:
            d[c or "(无 err_code)"] = d.get(c or "(无 err_code)", 0) + 1
        print("   失败按 err_code:", json.dumps(d, ensure_ascii=False))
        for g, c, ts in failed[:10]:
            print("     - %-12s %s  %s" % (g, c, ts[:19]))
    if missing:
        print("   未对上样例:", missing[:12])
    if a.json:
        print("\n" + json.dumps({"ok": ok, "queued": [g for g, _ in queued],
                                 "failed": [g for g, _, _ in failed],
                                 "missing": missing}, ensure_ascii=False, indent=1))
    return 0 if not failed and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
