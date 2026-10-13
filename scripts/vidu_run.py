# -*- coding: utf-8 -*-
"""vidu_run.py —— Vidu「参考生视频」批量提交器（★ 本渠道唯一入口）。

═══ 参数化边界（用户要求）═══
  当参数：**图片路径列表 / 音频路径列表 / 提示词文案 / 时长 / 宽高比**（从 jobs_vidu.json 读）
  固定代码：其余一切（打开页面、找入口、上传、选模型、点创作、节流、留痕）

═══ 模块分工 ═══
  vidu_log   留痕        vidu_js      JS 片段
  vidu_ops    页面原语     vidu_mentions mention 机制
  vidu_submit 提交+网络确认   vidu_run     ← 本文件：编排

═══ 用法 ═══
  python vidu_run.py --jobs <jobs_vidu.json> --job 1_1_标准 --mode upload-only
  python vidu_run.py --jobs <jobs_vidu.json> --job 1_1_标准 --mode submit
  python vidu_run.py --jobs <jobs_vidu.json> --all --skip "1_1_标准" --mode submit

  --mode   probe=只上传+报 mention（不写提示词）／upload-only=填完不提交／submit=提交
  --reload auto(默认)｜never｜always  —— 见 run_one 里"组间复位"的说明
  ⚠ 必须用**系统 Python 3.14**跑（`websocket-client` 只装在那个解释器里）：
     "C:/Users/Administrator/AppData/Local/Microsoft/WindowsApps/python.exe" vidu_run.py ...

═══ 平台机制 ═══
  全部硬事实（页面渲染 / 上传 / mention / 设置区 / 定位坑 / 提交确认）见
  **`references/vidu-平台机制.md`**；浏览器与登录态见 `INIT.md`。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vidu_cdp import CDP            # noqa: E402
from vidu_js import JS_SET_BLOCKS, JS_COLLECT_MENTIONS, JS_EDITOR_TEXT  # noqa: E402
from vidu_log import STATE_DIR, log  # noqa: E402
from vidu_mentions import build_blocks, wait_mentions  # noqa: E402
from vidu_ops import (apply_settings, ensure_clean, file_input_object,  # noqa: E402
                      upload_audio)
from vidu_submit import NetWatch, click_submit  # noqa: E402

URL = "https://www.vidu.cn/create/character2video"


def run_one(c, job, mode, cfg):
    jid = job["id"]
    # ★ 作业单字段可能叫 audios（新，vidu_make_jobs.py 产出）或 voices（旧）——两种都吃
    vi_list = job.get("audios") or job.get("voices") or []
    print("\n=== 组 %s  (图%d/音%d/时长%s) ===" % (jid, job["n_images"], len(vi_list), job["duration"]))

    # 0) 页面就位（**默认不刷新** —— 用户明确要求"就在已开的页面继续操作"）
    # ★★ 平台坑（2026-10-07 实测更正，之前把锅扣给 `Page.navigate` 是误判）：
    #   **Chromium 会节流后台标签** —— Vidu 创作页主内容区依赖 rAF / 尺寸测量来挂载，
    #   标签不在前台就**永远停在只渲染侧栏**（bodyLen=38、fileInput=0、tiptap 不存在），
    #   但此时 `document.body.innerHTML` 已有 46 万字符 ⇒ 内容在，只是没渲染。
    #   对策：`Emulation.setFocusEmulationEnabled(True)` + 每轮 `Page.bringToFront`
    #   （实测 3 秒内 bodyLen 38→17385）。**这两条与刷不刷新无关，必须做。**
    #
    # ★ 刷新只为"组间复位"：上一组的图片若还挂在资产栏，新上传的会接着编号成 图8/图9…，
    #   `pnN → 图N` 的映射就整体错位。所以：
    #     --reload never  完全不动当前页（单组调试）
    #     --reload auto   只在表单脏（有文本或有 mention）时才刷（默认）
    #     --reload always 每组都刷
    did = ensure_clean(c, cfg.get("reload", "auto"))
    t0 = time.time()
    ready = False
    while time.time() - t0 < 90:
        try:
            c.send("Page.bringToFront")
            c.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception:
            pass
        time.sleep(3)
        try:
            bl = c.eval("document.body ? document.body.innerText.length : -1") or 0
            fi = c.eval("document.querySelectorAll('input[type=file]').length") or 0
            if bl > 3000 and fi > 0:
                ready = True
                break
        except Exception:
            pass
    print("   页面:%s" % did)
    print("   页面就绪:", c.eval("location.href"),
          " bodyLen=%s fileInput=%s" % (c.eval("document.body.innerText.length"),
                                        c.eval("document.querySelectorAll('input[type=file]').length")))
    if not ready:
        log({"job": jid, "error": "页面未挂载（bodyLen 太小）—— 可能 SPA 卡住，需人工重载"})
        return False

    # 1) 图片（入口按 accept 判定；投喂必须用 objectId，nodeId 会被 SPA 重渲染作废）
    # ★★ 缺图必须中止：少传一张会让 `pnN → 图N` **整体错位**（第 4 个资产变成 图3），
    #    而且不会报错 —— 只会静默产出一条绑定错资产的片子。
    missing = [x["path"] for x in job["images"] if not os.path.exists(x["path"])]
    if missing:
        log({"job": jid, "error": "缺图 —— 中止本组，不提交", "missing": missing})
        print("   ✗ 缺 %d 张图 —— 中止本组" % len(missing))
        return False
    imgs = [x["path"] for x in job["images"]]
    oid, acc = file_input_object(c, "image")
    if oid is None:
        log({"job": jid, "error": "找不到图片 input"})
        return False
    c.send("DOM.setFileInputFiles", {"objectId": oid, "files": imgs})
    print("   上传图片 %d 张（accept=%s）" % (len(imgs), acc))

    # 2) 音频（DOM 里查不到 input ⇒ 必须走「真实点按钮 + 拦 fileChooserOpened」；上限 3）
    # ★ 修 bug（2026-10-07）：旧代码要求 `v.get("exists")`，而新作业单没有该键 ⇒ 音色被静默跳过。
    #   现在只看路径是否真的在磁盘上；**缺音色同样中止**（少一个会让 vmN ↔ 音频N 错位）。
    auds, aud_missing = [], []
    for k, v in enumerate(vi_list, 1):
        p = v.get("path")
        (auds.append(p) if (p and os.path.exists(p)) else aud_missing.append((k, v.get("speaker"), p)))
    if aud_missing:
        log({"job": jid, "error": "缺音色文件 —— 中止本组，不提交", "missing": aud_missing})
        print("   ✗ 缺 %d 个音色文件 —— 中止本组" % len(aud_missing))
        return False
    n_aud_expect = 0
    if auds:
        ok, info = upload_audio(c, auds)
        print("   上传音频 %d 个 ->" % len(auds), json.dumps(info, ensure_ascii=False))
        if not ok:
            # ★★ 硬中断（2026-10-07 修）：实测 `2_1_标准` 音频上传偶发失败但仍继续提交，
            #    结果**静默产出一条没有音色参考的片子**。缺资产必须中止本组、交给重跑，
            #    绝不"带伤提交"。
            log({"job": jid, "error": "音频上传失败 —— 中止本组，不提交（请重跑）", "info": info})
            print("   ✗ 音频上传失败（已重试）—— 中止本组，不提交")
            return False
        n_aud_expect = min(len(auds), 3)
        time.sleep(3)

    # 3) 等平台把资产自动插成 mention，并把 attrs 收集起来（preview/id 都在里面）
    n_expect = len(imgs) + n_aud_expect
    mentions, m_ok = wait_mentions(c, n_expect, timeout=150)
    print("   ★ 全部 mention（%d/%d）:" % (len(mentions), n_expect),
          json.dumps({k: v.get("mediaType") for k, v in mentions.items()}, ensure_ascii=False))
    if not m_ok:
        log({"job": jid, "error": "mention 数未达预期 —— 中止本组，不提交（请重跑）",
             "got": sorted(mentions.keys()), "expect": n_expect})
        print("   ✗ mention 数未达预期 —— 中止本组，不提交")
        return False

    if mode == "probe":
        log({"job": jid, "mode": "probe",
             "mentions": {k: v.get("mediaType") for k, v in mentions.items()}})
        return True

    # 4) 用真 mention 重建提示词 —— 决定性一步：
    #    平台默认把资产节点堆在内容末尾；这里按 `名称=@` 的位置把它们逐个放回去。
    blocks, used, rest = build_blocks(job["prompt"], imgs, auds, mentions)
    spec = {"blocks": blocks, "attrs": mentions}
    r = c.eval(JS_SET_BLOCKS + "(%s)" % json.dumps(spec, ensure_ascii=False))
    print("   重建文档:", r, "｜mention 共 %d（图 %d + 音 %d），未绑定 %s"
          % (len(used), len(imgs), len(auds), rest or "无"))
    time.sleep(2)

    # 5) 复核：编辑器里现在有哪些 mention、正文有多少字
    chk = c.eval(JS_COLLECT_MENTIONS) or {}
    has = sorted((chk.get("mentions") or {}).keys())
    txt = c.eval(JS_EDITOR_TEXT) or ""
    print("   复核 mention:", has, " 编辑器内共 %d 字" % len(txt))
    if len(has) < len(mentions):
        log({"job": jid, "warn": "重建后 mention 变少", "before": sorted(mentions.keys()), "after": has})

    # 6) 设置：模型 / 时长 / 宽高比 / 数量
    st = apply_settings(c, job, cfg)
    for k, v in st.items():
        print("   %s ->" % k, json.dumps(v, ensure_ascii=False))
        if not v.get("ok"):
            log({"job": jid, "warn": "设置失败: %s" % k, "detail": v})
    # ★★ 2026-10-07：核心设置（模型/时长/宽高比）失败**必须中止**，绝不能带着错参数提交。
    #   实测教训：时长条是轮播，格子滚出可视区时点击落在箭头上**静默无效**，
    #   旧代码只打一句 warn 就继续提交 ⇒ 声明 12s 的 11 组全部按默认 8s 出片，
    #   成片短 4 秒、台词被截，事后只能靠比对 settings.duration 才发现。
    bad = [k for k in ("模型", "时长", "宽高比") if k in st and not st[k].get("ok")]
    if bad:
        print("   ✗ 核心设置未生效：%s ⇒ 中止本组，不提交" % bad)
        log({"job": jid, "mode": mode, "status": "ABORT_SETTINGS", "bad": bad, "detail": st})
        return False

    shot = os.path.join(STATE_DIR, "shots", "vidu_%s_%s.png" % (jid, mode))
    os.makedirs(os.path.dirname(shot), exist_ok=True)
    try:
        c.screenshot(shot)
        print("   截图:", shot)
    except Exception as e:
        print("   截图失败:", type(e).__name__)

    if mode != "submit":
        log({"job": jid, "mode": mode, "status": "READY_NOT_SUBMITTED"})
        return True

    # 7) 创作 —— ★ 用旁路网络监听确认"真的提交了"（按钮 aria-disabled 期间点击会静默无效）
    watch = NetWatch(port=cfg.get("port", 9222), url_contains="vidu.cn")
    watch.start()
    try:
        r = click_submit(c, watch)
    finally:
        watch.stop()
    if r.get("ok"):
        log({"job": jid, "mode": mode, "status": "SUBMITTED", "click": r})
        print("   ★ 已提交（网络已确认）->", json.dumps(r, ensure_ascii=False)[:260])
        return True
    log({"job": jid, "mode": mode, "status": "SUBMIT_FAILED", "click": r})
    print("   ✗ 提交失败 ->", json.dumps(r, ensure_ascii=False)[:300])
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--job", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--skip", default="", help="逗号分隔的组 id，跳过（已提交过的组）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--mode", choices=["probe", "upload-only", "submit"], default="upload-only")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--pause", type=float, default=3.0, help="组间间隔秒")
    ap.add_argument("--reload", choices=["auto", "always", "never"], default="auto",
                    help="组间复位策略：auto=只在表单脏时刷（默认）／never=完全不动当前页／always=每组都刷")
    a = ap.parse_args()

    d = json.load(open(a.jobs, encoding="utf-8"))
    jobs = d["jobs"]
    cfg = {"model": "Vidu Q4 预览版", "ratio": "9:16", "reload": a.reload, "port": a.port}

    if a.job:
        jobs = [j for j in jobs if j["id"] == a.job]
    elif a.all:
        if a.skip:
            skip = {s.strip() for s in a.skip.split(",") if s.strip()}
            before = len(jobs)
            jobs = [j for j in jobs if j["id"] not in skip]
            print("★ --skip 跳过 %d 组：%s" % (before - len(jobs), sorted(skip)))
    else:
        print("★ 要指定 --job <id> 或 --all"); return 1
    if a.limit:
        jobs = jobs[:a.limit]
    if not jobs:
        print("★ 没有匹配的组"); return 1

    print("计划处理 %d 组，mode=%s" % (len(jobs), a.mode))
    ok = 0
    with CDP(port=a.port, url_contains="vidu.cn", timeout=40) as c:
        for i, job in enumerate(jobs, 1):
            print("\n---- [%d/%d] ----" % (i, len(jobs)))
            try:
                if run_one(c, job, a.mode, cfg):
                    ok += 1
            except Exception as e:
                log({"job": job["id"], "error": "%s: %s" % (type(e).__name__, e)})
                print("   ★ 异常:", type(e).__name__, e)
            if i < len(jobs):
                time.sleep(a.pause)
    print("\n完成 %d/%d 组（mode=%s）" % (ok, len(jobs), a.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
