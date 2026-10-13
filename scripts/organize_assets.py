# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:1e3fe5a029 · 溯源标记，请保留
"""organize_assets.py —— 把客户"乱"的项目目录归位到标准结构（★ 只搬不删）

背景
----
客户拿来的项目目录往往是这样：我们的标准目录旁边，散着一堆
「护士三视图.png / 人物定妆照 / 人物介绍.docx / 故事板1.png / 视频素材/ …」。
本工具按**类型 + 关键词**把它们归位，并给出可读的《整理方案》。

★ 安全约定
----------
1. **默认只出方案（dry-run）**，不动任何文件；看清了再加 `--apply` 才搬。
2. **只搬不删** —— 永远不删除用户文件；重名自动加 `_2`、`_3`。
3. 用户原图**同时留一份**在 `01_资产库/用户上传/`（规范要求：不覆盖/不"标准化"用户原图）。
4. 认不出来的 → 列进「待确认」，⛔ 不猜、不动。

用法
----
  python scripts/organize_assets.py --project "<项目目录>"              # 只出方案
  python scripts/organize_assets.py --project "<项目目录>" --apply       # 执行搬运
  python scripts/organize_assets.py --project "<项目目录>" --ep 1        # 指定集号（故事板归档用）

输出：统一 JSON（plan / applied / skipped / unknown / report）。
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ssr_core as C  # noqa: E402

# 我们自己的标准顶层（这些不动）
STANDARD_TOP = {"00_剧本与提示词", "01_资产库", "02_分集工程", "03_背景音乐",
                "04_最终交付", "_出片记录", "_废案"}
STANDARD_FILES = {"_项目档案.json", "_mjstory_project.json", "README.md"}
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
AUD_EXT = (".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg")
VID_EXT = (".mp4", ".mov", ".mkv", ".avi")
DOC_EXT = (".docx", ".doc", ".txt", ".md", ".pdf")

# 关键词 → 归类
KW_STORYBOARD = ("故事板", "storyboard", "分镜图")
KW_SCRIPT = ("剧本", "分集", "大纲", "人物小传", "人物介绍", "设定", "世界观")
KW_VOICE = ("音色", "配音", "voice")


def _target(rel, ep):
    """返回目标相对路径；None = 认不出（待确认）。"""
    low = rel.lower()
    name = os.path.basename(rel)
    ext = os.path.splitext(name)[1].lower()
    if any(k in name for k in KW_STORYBOARD) and ext in IMG_EXT:
        return os.path.join("00_剧本与提示词", "EP%02d" % ep, "故事板", name)
    if any(k in name for k in KW_SCRIPT) and (ext in DOC_EXT or ext in IMG_EXT):
        return os.path.join("01_资产库", "用户上传", name)
    if any(k in name for k in KW_VOICE) and ext in AUD_EXT:
        return os.path.join("01_资产库", "音色", name)
    if low.startswith("剧本") and ext in DOC_EXT:
        return os.path.join("00_剧本与提示词", "剧本", name)
    if ext in IMG_EXT:
        return os.path.join("01_资产库", "用户上传", name)      # 图（三视图/定妆照/角色图…）
    if ext in AUD_EXT:
        return os.path.join("01_资产库", "音色", name)
    if ext in VID_EXT:
        return os.path.join("01_资产库", "视频素材", name)
    if ext in DOC_EXT:
        return os.path.join("01_资产库", "用户上传", name)
    return None


def _unique(dst):
    if not os.path.exists(dst):
        return dst
    b, e = os.path.splitext(dst)
    i = 2
    while os.path.exists("%s_%d%s" % (b, i, e)):
        i += 1
    return "%s_%d%s" % (b, i, e)


def scan(project, ep):
    """只扫**顶层**（含顶层目录内的散文件），不动标准目录内部。"""
    plan, unknown, keep = [], [], []
    for name in sorted(os.listdir(project)):
        p = os.path.join(project, name)
        if name in STANDARD_TOP or name in STANDARD_FILES:
            keep.append(name)
            continue
        if os.path.isdir(p):
            # 顶层目录：把里面的文件按规则归位（只下探 2 层，避免拖太大）
            inner = []
            for b, dns, fns in os.walk(p):
                if b[len(p):].count(os.sep) > 1:
                    dns[:] = []
                    continue
                for f in fns:
                    inner.append(os.path.relpath(os.path.join(b, f), project))
            if not inner:
                plan.append({"from": name + os.sep, "to": os.path.join("01_资产库", "用户上传", name) + os.sep,
                             "kind": "dir", "why": "顶层散落目录 → 用户上传"})
            for rel in inner:
                t = _target(rel, ep)
                (plan.append({"from": rel, "to": t, "kind": "file", "why": "关键词/扩展名"})
                 if t else unknown.append(rel))
        else:
            t = _target(name, ep)
            (plan.append({"from": name, "to": t, "kind": "file", "why": "关键词/扩展名"})
             if t else unknown.append(name))
    return plan, unknown, keep


def apply_plan(project, plan, log):
    done, failed = [], []
    for it in plan:
        src = os.path.join(project, it["from"].rstrip("\\/"))
        dst = os.path.join(project, it["to"].rstrip("\\/"))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if it["kind"] == "dir":
                if os.path.exists(dst):
                    dst = _unique(dst)
                shutil.move(src, dst)          # 目录整体搬走（不删）
            else:
                dst = _unique(dst)
                shutil.move(src, dst)          # 文件搬走（不删）
            done.append({"from": it["from"], "to": os.path.relpath(dst, project)})
        except Exception as e:
            failed.append({"from": it["from"], "error": str(e)})
    return done, failed


def main():
    ap = argparse.ArgumentParser(prog="organize_assets")
    ap.add_argument("--project", required=True, help="项目目录")
    ap.add_argument("--apply", action="store_true", help="真的执行搬运（默认只出方案）")
    ap.add_argument("--ep", type=int, default=1, help="集号（故事板归档用），默认 1")
    a = ap.parse_args()

    project = os.path.abspath(a.project)
    if not os.path.isdir(project):
        C.fail("E_INPUT", "项目目录不存在：%s" % project)

    plan, unknown, keep = scan(project, a.ep)
    log, done, failed = [], [], []
    leftover = []
    if a.apply and plan:
        done, failed = apply_plan(project, plan, log)
        # ★ 只报告"搬空后剩下的空目录"，⛔ 不删（交给用户自己决定）
        for name in sorted(os.listdir(project)):
            p = os.path.join(project, name)
            if (name not in STANDARD_TOP and name not in STANDARD_FILES
                    and os.path.isdir(p) and not os.listdir(p)):
                leftover.append(name)
        if leftover:
            log.append("搬空后剩 %d 个空目录（**未删**，可自行删除）：%s"
                       % (len(leftover), "、".join(leftover)))

    rep = ["# 资产整理方案", "",
           "- 项目：%s" % project,
           "- 时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
           "- 模式：%s" % ("**已执行搬运**" if a.apply else "**仅方案（未动文件）**"), "",
           "| # | 现位置 | → 归到 | 依据 |", "|---|---|---|---|"]
    for i, it in enumerate(plan, 1):
        rep.append("| %d | `%s` | `%s` | %s |" % (i, it["from"], it["to"], it["why"]))
    if unknown:
        rep += ["", "## 待确认（⛔ 未动，请人工判断）", ""] + ["- `%s`" % u for u in unknown]
    rep += ["", "## 保持不动", ""] + ["- `%s`" % k for k in keep]
    report = "\n".join(rep)

    # 落档（便于留痕）
    try:
        d = os.path.join(project, "_出片记录")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "资产整理方案.md"), "w", encoding="utf-8") as f:
            f.write(report)
    except Exception as e:
        log.append("写报告失败（可忽略）：%s" % e)

    # 顺手把"用户上传"下的图复制一份到 图片/（规范：原图留档 + 可用副本）
    if a.apply:
        try:
            up = os.path.join(project, "01_资产库", "用户上传")
            pic = os.path.join(project, "01_资产库", "图片")
            os.makedirs(pic, exist_ok=True)
            n = 0
            for b, dns, fns in os.walk(up):
                for f in fns:
                    if f.lower().endswith(IMG_EXT):
                        d = _unique(os.path.join(pic, f))
                        shutil.copyfile(os.path.join(b, f), d)   # 复制，不动原件
                        n += 1
            log.append("已把 %d 张用户图**复制**一份到 01_资产库/图片/（原件仍在 用户上传/）" % n)
        except Exception as e:
            log.append("复制用户图失败（可忽略）：%s" % e)

    print(json.dumps({"status": "ok", "result": {
        "project": project, "dry_run": not a.apply,
        "planned": len(plan), "applied": done, "failed": failed,
        "unknown": unknown, "kept": keep, "log": log,
        "report": os.path.join(project, "_出片记录", "资产整理方案.md"),
        "report_text": report,
    }, "error_code": None,
        "message": "已出整理方案（未动文件）；确认后加 --apply 执行" if not a.apply else "整理完成（只搬不删）",
        "retryable": False, "runtime_version": C.RUNTIME_VERSION}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
