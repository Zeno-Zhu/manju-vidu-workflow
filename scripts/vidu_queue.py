# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:2d2e3157eb · 溯源标记，请保留
"""vidu_queue.py —— Vidu「闲时额度」队列调度器（★ 任务 2）。

═══ 它解决什么 ═══
Vidu 闲时（`schedule_mode="nopeak"`）**每天 200 条、不消耗积分**，但**同时进行中最多 100 条**
⇒ 想"把每天的闲时额度榨干"，不能一次性把 200 条全推上去（多余的会被拒/排队异常），
必须**盯着平台侧的"进行中"数量，掉下来一个就补投一个**。

本模块把这件事做成**固定代码 + 参数**，AI 只需给一个作业单和几个上限：

    vidu_queue.py init   --jobs <jobs_vidu.json>          # 建队列（读作业单，全是 pending）
    vidu_queue.py run    --concurrency 100 --daily 200    # 跑调度循环（前台/后台皆可）
    vidu_queue.py run    --once                           # 只跑一轮（配 cron / 定时器）
    vidu_queue.py status                                  # 看队列 + 平台侧实时水位

★★ 参数就是全部可变量（用户要求"代码自己组装、不要 AI 写代码"）：
   · `--concurrency`  同时进行中上限（默认 100）
   · `--daily`        当天提交上限（默认 200）
   · `--interval`     轮询间隔秒（默认 60）
   · `--jobs`         作业单（唯一决定"提交什么"的文件，格式见 SKILL §3）
   · `--mode`         `nopeak`（闲时，默认）/ `normal`（消耗积分）

★ 水位以**平台为准**（`GET /vidu/v1/tasks/history/me`）＋ **本地台账兜底**（刚提交的还没进
  "进行中"就已经算数）⇒ 取两者较大值，避免超投。

⚠ 必须用**系统 Python 3.14**、**沙箱外**跑；浏览器实例要先起（`vidu_edge.py up`）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vidu_api import (call, render_prompt, img_spec, audio_spec,  # noqa: E402
                      build_settings)
from vidu_log import log   # noqa: E402

DEFAULT_LIMITS = {"concurrency": 100, "daily": 200}
STATE_NAME = "_vidu_queue.json"


# ---------------------------------------------------------------- 队列读写
def queue_path(jobs_file, explicit=""):
    if explicit:
        return explicit
    proj = os.path.dirname(os.path.dirname(os.path.abspath(jobs_file)))
    d = os.path.join(proj, "_出片记录")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, STATE_NAME)


def load_queue(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_queue(path, q):
    q["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=1)


def today():
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 平台水位
def platform_state(port=9222):
    d = call({"op": "state", "todayPrefix": today()}, port=port, timeout=180)
    if d.get("error"):
        raise RuntimeError("查平台状态失败: %s" % d["error"])
    # ★★ 顺带取**权威的闲时额度** —— 页面左下角「用量 used/total」同源：
    #    GET /vidu/v1/tasks/quota → nopeak_quota:{used,total}
    #    （2026-10-13 实测；比"数今天任务"准，也不受 created_at 时区影响）
    try:
        qd = call({"op": "quota"}, port=port, timeout=90)
        nq = qd.get("nopeak_quota") or {}
        if nq:
            d["nopeak_used"] = int(nq.get("used") or 0)
            d["nopeak_total"] = int(nq.get("total") or 0)
            d["nopeak_left"] = max(0, d["nopeak_total"] - d["nopeak_used"])
    except Exception:
        pass
    return d


def _stop_for_quota(q, path, pend, used, daily, conc, inflight, a):
    """★★ 闲时额度用完 ⇒ **停止作业**（不再空转等待），写状态 + 给出可执行的下一步。

    为什么必须"停"而不是"等"：
      额度当天用完后**不会自己恢复**（0 点才重置）。旧代码在这里 `sleep(interval)` 死等，
      结果是**整夜空转**、用户以为还在跑 —— 既浪费时间也让人误判进度。
      正确做法：**停下、说清楚、给出选项、支持续跑**（队列里 submitted 的组天然跳过）。
    """
    now = datetime.now()
    q["daily"]["stopped_reason"] = "quota_exhausted"
    q["daily"]["stopped_at"] = now.isoformat(timespec="seconds")
    q["daily"]["stopped_used"] = used
    q["daily"]["stopped_daily"] = daily
    save_queue(path, q)
    log({"mode": "queue_stopped", "reason": "quota_exhausted",
         "used": used, "daily": daily, "pending": len(pend), "inflight": inflight})

    py = os.path.basename(sys.executable)
    sd = os.path.dirname(os.path.abspath(__file__))
    bar = "=" * 66
    print("\n" + bar)
    print(" ⛔ 闲时额度已用完 —— 已停止作业（不再空转等待）")
    print(bar)
    print(" 今日闲时 ：%d / %d（用完）" % (used, daily))
    print(" 进行中   ：%d（并发上限 %d，跑完不影响额度）" % (inflight, conc))
    print(" 未提交   ：%d 组 —— ★ 原样保留，不会丢" % len(pend))
    print(" 队列文件 ：%s" % path)
    print()
    print(" ⇒ 二选一：")
    print("    A. 【换账号】今天继续跑")
    print("       %s \"%s\\vidu_edge.py\" up --account 账号2" % (py, sd))
    print("       %s \"%s\\vidu_queue.py\" run --jobs <jobs_vidu.json>" % (py, sd))
    print("       ★ 队列里 submitted 的组会自动跳过，**只跑剩下的 %d 组**。" % len(pend))
    print()
    print("    B. 【等明天】额度 0 点重置")
    print("       明早重跑同一条 run 命令即可 —— 台账跳过已完成，接着跑。")
    print()
    print(" ⚠ 别反复重跑：额度没恢复时重跑会立刻再次停在这里（不影响已完成部分）。")
    print(bar + "\n")
    return 2          # ★ 退出码 2 = 额度耗尽（1=错误，0=正常结束）


# ---------------------------------------------------------------- 子命令
def cmd_init(a):
    jobs = json.load(open(a.jobs, encoding="utf-8"))
    path = queue_path(a.jobs, a.state)
    old = load_queue(path)
    if old and not a.force:
        print("★ 队列已存在：%s（要重建加 --force）" % path)
        return 1
    items = [{"id": j["id"], "status": "pending", "task_id": "", "at": "", "err": ""}
             for j in jobs["jobs"]]
    q = {"project": os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(a.jobs)))),
         "jobs_file": os.path.abspath(a.jobs), "mode": a.mode,
         "limits": {"concurrency": a.concurrency, "daily": a.daily},
         "created_at": datetime.now().isoformat(timespec="seconds"),
         "daily": {"date": today(), "submitted": 0},
         "items": items}
    save_queue(path, q)
    print("队列已建：%s\n  %d 组｜mode=%s｜并发上限 %d｜当日上限 %d"
          % (path, len(items), a.mode, a.concurrency, a.daily))
    return 0


def _roll_day(q):
    if q.get("daily", {}).get("date") != today():
        q["daily"] = {"date": today(), "submitted": 0}
    return q


def cmd_status(a):
    path = queue_path(a.jobs, a.state) if a.jobs else a.state
    q = load_queue(path)
    if not q:
        print("★ 没有队列：%s" % path); return 1
    _roll_day(q)
    cnt = {}
    for it in q["items"]:
        cnt[it["status"]] = cnt.get(it["status"], 0) + 1
    print("队列 %s\n  项目=%s  mode=%s  上限 并发%s/当日%s"
          % (path, q.get("project"), q.get("mode"),
             q["limits"]["concurrency"], q["limits"]["daily"]))
    print("  本地台账: %s（今日已提交 %d）" % (json.dumps(cnt, ensure_ascii=False),
                                              q["daily"]["submitted"]))
    try:
        st = platform_state(a.port)
        # ★ 当日已用取「本地台账」与「平台侧今日闲时」的**较大值**（本地可能丢数）
        used = q["daily"]["submitted"]
        if q.get("mode") == "nopeak":
            used = max(used, st.get("today_nopeak", 0))
        print("  平台侧  : 进行中 %s｜今日闲时 %s｜任务总数 %s"
              % (st.get("inflight"), st.get("today_nopeak"), st.get("total_tasks")))
        print("  状态分布: %s" % json.dumps(st.get("by_state"), ensure_ascii=False))
        print("  ★ 可再投 = min(并发 %d − 进行中 %d, 当日 %d − 已用 %d) = %d"
              % (q["limits"]["concurrency"], st.get("inflight", 0),
                 q["limits"]["daily"], used,
                 max(0, min(q["limits"]["concurrency"] - st.get("inflight", 0),
                            q["limits"]["daily"] - used))))
    except Exception as e:
        print("  ⚠ 平台侧查不到（%s）—— 先用本地台账" % e)
    return 0


def _submit_one(job, q, port):
    text = render_prompt(job["prompt"])
    imgs = [img_spec(x["path"], x.get("name")) for x in job.get("images", [])]
    aud_list = (job.get("audios") or [])[:3]
    auds = [audio_spec(x["path"], x.get("speaker")) for x in aud_list]
    miss = [x["path"] for x in job.get("images", []) if not os.path.exists(x["path"])] \
        + [x["path"] for x in aud_list if not os.path.exists(x["path"])]
    if miss:
        return False, "缺文件 %s" % miss[:2]
    spec = {"op": "submit", "text": text, "images": imgs, "audios": auds, "subjects": [],
            "settings": build_settings(job.get("duration", 5), job.get("ratio", "9:16"), q["mode"])}
    d = call(spec, port=port)
    if d.get("status") == 200:
        return True, d.get("task_id")
    return False, (d.get("error") or ("HTTP %s" % d.get("status")))[:200]


def cmd_run(a):
    path = queue_path(a.jobs, a.state) if a.jobs else a.state
    q = load_queue(path)
    if not q:
        print("★ 没有队列，先 init"); return 1
    jobs_all = {j["id"]: j for j in json.load(open(q["jobs_file"], encoding="utf-8"))["jobs"]}
    conc = a.concurrency or q["limits"]["concurrency"]
    daily = a.daily or q["limits"]["daily"]
    q["limits"] = {"concurrency": conc, "daily": daily}
    _roll_day(q)

    print("调度开始：并发≤%d 当日≤%d 间隔%ss mode=%s%s"
          % (conc, daily, a.interval, q["mode"], "（--once 只跑一轮）" if a.once else ""))
    round_no = 0
    while True:
        round_no += 1
        q = _roll_day(load_queue(path) or q)
        inflight = 0
        st = {}
        try:
            st = platform_state(a.port)
            inflight = st.get("inflight", 0)
            # 平台侧今日闲时数作为**下限**校准（本地可能因异常丢数）
            if q["mode"] == "nopeak":
                q["daily"]["submitted"] = max(q["daily"]["submitted"], st.get("today_nopeak", 0))
        except Exception as e:
            print("  ⚠ 平台状态取不到（%s）；按本地台账继续" % e)

        used = q["daily"]["submitted"]
        conc_left = conc - inflight      # 并发出位余量
        quota_left = daily - used        # 今日闲时额度余量
        # ★★ **权威额度优先**：平台 `nopeak_quota` 是它自己算的（= 页面左下「用量」），
        #    比"本地台账"和"数今天任务"都准 —— 有它就以它为准。
        if q["mode"] == "nopeak" and st.get("nopeak_left") is not None:
            quota_left = st["nopeak_left"]
            used = st.get("nopeak_used", used)
        slots = min(conc_left, quota_left)
        pend = [it for it in q["items"] if it["status"] == "pending"]
        qt = ("闲时 %s/%s" % (st.get("nopeak_used"), st.get("nopeak_total"))
              if st.get("nopeak_total") is not None else "闲时 ?/?")
        print("[%s] 第%d轮  %s  进行中=%d  可投=%d  待办=%d"
              % (datetime.now().strftime("%H:%M:%S"), round_no, qt, inflight,
                 max(0, slots), len(pend)))

        if slots <= 0:
            # ★★ 关键：**两种"投不出去"性质完全不同，必须分开处理**
            #   · 并发满(conc_left<=0) ⇒ 任务跑完会释放 ⇒ **该等**（正常节奏）
            #   · 额度用完(quota_left<=0) ⇒ 今天不会自己恢复 ⇒ **该停**，交用户决策
            #     （旧代码在这里无脑 sleep 等待 ⇒ 程序整夜空转，用户还以为在跑）
            if quota_left <= 0 and pend:
                return _stop_for_quota(q, path, pend, used, daily, conc, inflight, a)
            if a.once:
                break
            time.sleep(a.interval)
            continue
        if not pend:
            print("★ 队列已跑完（没有 pending）")
            break

        for it in pend[:slots]:
            job = jobs_all.get(it["id"])
            if not job:
                it["status"] = "failed"; it["err"] = "作业单里找不到该组"
                continue
            ok, info = _submit_one(job, q, a.port)
            if ok:
                it.update({"status": "submitted", "task_id": info,
                           "at": datetime.now().isoformat(timespec="seconds"), "err": ""})
                q["daily"]["submitted"] += 1
                print("   ✓ %-16s task=%s" % (it["id"], info))
            else:
                it.update({"status": "failed", "err": info,
                           "at": datetime.now().isoformat(timespec="seconds")})
                print("   ✗ %-16s %s" % (it["id"], info))
            save_queue(path, q)
            log({"mode": "queue_submit", "job": it["id"], "task": it.get("task_id"),
                 "status": it["status"], "sched": q["mode"]})
            time.sleep(a.pause)
        save_queue(path, q)
        if a.once:
            break
        time.sleep(a.interval)
    print("结束。队列：%s" % path)
    return 0


def cmd_mark(a):
    path = queue_path(a.jobs, a.state) if a.jobs else a.state
    q = load_queue(path)
    if not q:
        print("★ 没有队列"); return 1
    n = 0
    for it in q["items"]:
        if it["id"] in {s.strip() for s in a.job.split(",") if s.strip()}:
            it["status"] = a.status
            n += 1
    save_queue(path, q)
    print("已改 %d 条 -> %s" % (n, a.status))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Vidu 闲时额度队列调度器")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--state", default="", help="队列文件路径（默认 <项目>/_出片记录/_vidu_queue.json）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--jobs", required=True)
    p.add_argument("--mode", choices=["nopeak", "normal"], default="nopeak")
    p.add_argument("--concurrency", type=int, default=DEFAULT_LIMITS["concurrency"])
    p.add_argument("--daily", type=int, default=DEFAULT_LIMITS["daily"])
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("status"); p.add_argument("--jobs", default="")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("run")
    p.add_argument("--jobs", default="")
    p.add_argument("--concurrency", type=int, default=0)
    p.add_argument("--daily", type=int, default=0)
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--pause", type=float, default=1.0)
    p.add_argument("--once", action="store_true")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("mark")
    p.add_argument("--jobs", default=""); p.add_argument("--job", required=True)
    p.add_argument("--status", required=True)
    p.set_defaults(fn=cmd_mark)

    a = ap.parse_args()
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
