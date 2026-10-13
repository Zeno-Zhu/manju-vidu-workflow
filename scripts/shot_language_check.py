# -*- coding: utf-8 -*-
"""shot_language_check.py — 跨渠道「镜头语言」体检门（零计费、纯本地）

解决什么
--------
把原先只服务 H3 线的两项能力，与「镜头语言最小要求」合并成**一个渠道无关的门**：

  · 组内构图覆盖        （原 `h3_shot_coverage.py`     —— 防整组内反打 / 连续同构图）
  · 跨组构图回声        （原 `h3_composition_echo.py`  —— 全季高频套件 / 相邻组连续同首镜）
  · 镜头语言最小要求    （原只写在 `prompts/即梦20镜头写作避坑.md` —— 每镜景别+机位 / 运镜占比 / 特写落点）

支持**两种提示词语法**，自动识别：

  · H3 体系   ： `&&start … &&end` 分块，组内 `[Shot N]`，主体 `<Subject N>`
  · 即梦体系  ： `分镜组ID：` 分组，组内 `△` 镜头行
                （云鲸 / 器灵 / libtv / 迪拜 / 锦行 / 即梦2.0 共用这套写法）

中英双语词表（景别 / 机位角度 / 运镜与机位行为）。

判据（权威 = skill `manju-creation/references/镜头语言.md`（跨制式通用层）
      ＋ `prompts/即梦20镜头写作避坑.md`（即梦系 10 条）＋ `prompts/H3多参提示词.md` §9）
--------------------------------------------------------------------------------
  阻断（退出码 1）
    A. 有镜头**没写景别**
    A2. 有镜头**没写运镜状态**（既无运动词也无固定词）—— 「不写运镜」≠「固定机位」，
        固定也要写 `static camera` / 机位固定（沉默不是指令）
    ~~C. 运镜/机位行为覆盖率 ≥60%~~ ★ **2026-10-11 删除**：表面是"下限门"，本质是**创作配额** ——
        若一部戏本就该全用固定机位，它永远过不了门。覆盖率降级为**仅报告的参考值**。

  告警（**只呈现证据；`--strict` 下才计入退出码**）
    E. **单镜组**（整组只有 1 个镜头）—— 「单 △ 组是高危信号」
    F. 组内**连续 ≥ `--min-run`** 个同景别（默认 3）
    F2. 组内**连续 ≥ `--min-run`** 个同运镜（默认 3）              ★ 2026-10-11 新增
    G. **全季高频构图套件**：次数 ≥ `--suite-min`（默认 4）**且**占全季镜数 ≥ `--suite-pct`（默认 15%）
       ★ 2026-10-11 起**计入退出码**（原只打印在信息区）；★ **只算运动类运镜** ——
       固定机位是基线，重复不构成回声
    H. **相邻组首镜连续 ≥ `--run` 组同「景别＋运镜」**（默认 3）
       ★ 2026-10-11：**组首取值从「景别」扩为「景别＋运镜」**（原来只取景别，于是
       "每组首镜都在推"这类回声根本查不出来）；并计入退出码

  ⛔ 本工具**不设创作配额**：只回答"**有没有重复**"，**不规定"该用多少推镜"** ——
     推 / 微推 / 手持抖动都是**有情绪价值的工具**，选什么由剧情与上下分镜组决定。

  仅报告（不判定）
    B. 机位/角度覆盖率
    D. 景别分布与**特写数**（全篇参考 5–10 处）

用法
----
    python tools/shot_language_check.py "<项目>/00_剧本与提示词"
    python tools/shot_language_check.py "<项目>/00_剧本与提示词/第3集.txt"
    python tools/shot_language_check.py <目标> --json out.json --min-run 3
    python tools/shot_language_check.py <目标> --strict      # 告警也计入退出码（rc=1）

退出码（★ 2026-10-11 改三态）：0 = PASS（无阻断；**告警只呈现**）｜
      2 = BLOCK（结构性错误：字段缺失等确定性错误）｜
      1 = **仅 `--strict` 下出现**（告警也当门用）｜ 3 = 输入错误（没找到提示词文件）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

# ---------------------------------------------------------------- 词表（长短优先）
SHOT_SIZE = [
    # 中文（长词在前，避免「特写」吃掉「大特写」）
    "大特写", "中近景", "中全景", "特写", "近景", "中景", "全景", "远景", "过肩", "越肩", "全貌",
    # 英文
    "extreme close-up", "extreme closeup", "medium close-up", "medium closeup",
    "close-up", "closeup", "medium shot", "medium wide", "wide shot", "full shot",
    "establishing shot", "master shot", "bust shot", "close shot", "medium-full",
    "two-shot", "two shot", "three-shot", "three shot",
    "over-the-shoulder", "over the shoulder", "shoulder shot", "pov shot",
]

ANGLE = [
    "平视", "俯拍", "俯视", "仰拍", "仰视", "顶拍", "侧拍", "正面", "背面", "斜侧",
    "低角度", "高角度", "越肩", "过肩", "主观",
    "eye level", "eye-level", "low angle", "high angle", "dutch angle", "top-down",
    "overhead", "birds-eye", "bird's-eye", "profile shot", "frontal",
]

MOVE = [
    "缓推", "轻推", "微推", "推移", "跟拍", "跟随", "跟进", "跟移", "跟手", "横移", "横摇",
    "上摇", "下压", "上抬", "下移", "摇向", "环绕", "旋转", "升降", "变焦", "手持",
    "微俯", "略仰", "向后微跟", "推", "拉", "摇", "移", "跟随", "固定", "定机位", "机位固定",
    "static camera", "locked-off", "camera fixed", "push in", "pushes in", "pull back",
    "pulls back", "zoom in", "zoom out", "pans", "tilts", "tilt up", "tilt down",
    "tracking shot", "tracks", "dolly in", "dolly out", "handheld", "whip pan", "crane",
    "rack focus", "follows", "orbit", "arc around", "sweeps",
]

# 「固定也要显式写出来」——只写固定、没写给成什么行为，仍算写了机位行为
_FIXED_HINTS = ("固定", "机位固定", "定机位", "static camera", "locked-off", "camera fixed")

RE_H3_BLOCK = re.compile(r"&&start\s*\n(.*?)&&end", re.S)
# 组标识与时长：两种语法都收（H3 `group_id:` ｜ 即梦 `分镜组ID：`）
RE_ANY_GID = re.compile(r"(?:group_id|分镜组ID)\s*[:：]\s*(\S+)")
RE_ANY_DUR = re.compile(r"(?:group_duration|分镜组时长)\s*[:：]\s*(\d+)")
# 镜头行：H3 `[Shot N]` ｜ 即梦 `△ / ▸ / ▲ / ◆`（实测云鲸标准版用的是 ▸）
RE_H3_SHOT = re.compile(r"^\s*\[Shot\s+\d+\]")
RE_JM_SHOT = re.compile(r"^\s*[△▸▲◆]")
RE_ANY_SHOT = re.compile(r"^\s*(?:\[Shot\s+\d+\]|[△▸▲◆])")
RE_H3_DIA = re.compile(r"<d>")
# 即梦台词行：`说话人：台词 …<表演说明>`（容许全角 ＜＞，实测云鲸用全角）
RE_JM_DIA = re.compile(r"^[^\s△▸▲◆【#].{0,40}[：:].*[<＜].+[>＞]\s*$")
RE_H3_SUBJ = re.compile(r"<Subject\s+\d+>")
RE_JM_BODY = re.compile(r"【本?提示词正文(?:信息)?】")
RE_SPLIT_GID = re.compile(r"(?m)^(?=\s*(?:group_id|分镜组ID)\s*[:：])")


def find_phrase(line: str, vocab) -> str | None:
    """在行内按词表（已按长短排序）找第一个命中的词。中英通用。"""
    low = line.lower()
    for ph in vocab:
        if ph in low:
            return ph
    return None


# ------------------------------------------------------------------ 解析（双语法）
def _make_group(body: str, gid: str, dur, shots, dia, fmt: str):
    return {"gid": gid, "dur": dur, "shots": shots, "dia": dia,
            "subs": len(set(RE_H3_SUBJ.findall(body))), "fmt": fmt}


def _blocks(text: str):
    """尽量按 `&&start/&&end` 切块；没有就按组标识行切；再退化为整篇一块。"""
    blocks = [m.group(1) for m in RE_H3_BLOCK.finditer(text)]
    if not blocks:
        parts = RE_SPLIT_GID.split(text)
        blocks = [p for p in parts if RE_ANY_GID.search(p) or RE_ANY_SHOT.search(p)]
    return blocks or [text]


def parse_text(text: str):
    """自动识别语法并解析。★ 关键：块里若一个 `[Shot N]` 都没有、却有 △/▸ 行，
    就按即梦计（H3 外壳 + 即梦正文的混合形态，云鲸标准版就是这样）。"""
    groups = []
    for body in _blocks(text):
        gid_m = RE_ANY_GID.search(body)
        dur_m = RE_ANY_DUR.search(body)
        h3_shots = [ln.strip() for ln in body.splitlines() if RE_H3_SHOT.match(ln)]
        jm_shots = [ln.strip() for ln in body.splitlines() if RE_JM_SHOT.match(ln)]
        shots = h3_shots or jm_shots
        fmt = "h3" if h3_shots else ("jimeng" if jm_shots else "?")
        dia = len(RE_H3_DIA.findall(body))
        if not h3_shots:
            dia += sum(1 for ln in body.splitlines() if RE_JM_DIA.match(ln))
        groups.append(_make_group(
            body,
            gid_m.group(1) if gid_m else "?",
            int(dur_m.group(1)) if dur_m else None,
            shots, dia, fmt))
    has_shots = any(g["shots"] for g in groups)
    overall = "h3" if any(g["fmt"] == "h3" for g in groups) else "jimeng"
    if not has_shots:
        # 最后兜底：正文标记之后的 △/▸ 行整体当一组
        body_start = RE_JM_BODY.search(text)
        seg = text[body_start.end():] if body_start else text
        shots = [ln.strip() for ln in seg.splitlines() if RE_JM_SHOT.match(ln)]
        if shots:
            groups = [_make_group(seg, "(未分组)", None, shots, 0, "jimeng")]
            overall = "jimeng"
    return groups, overall


# ------------------------------------------------------------------------ 体检
def shot_profile(line: str):
    size = find_phrase(line, SHOT_SIZE)
    angle = find_phrase(line, ANGLE)
    move = find_phrase(line, MOVE)
    return size, angle, move


def analyze(groups, min_run: int = 3, suite_min: int = 4, suite_pct: float = 15.0):
    size_hist, move_hist, suite_hist = Counter(), Counter(), Counter()
    suite_sample, size_sample = {}, {}
    total_shots = 0
    no_size, has_angle, has_move, closeups = 0, 0, 0, 0
    blocks, warns, blocks_list = [], [], []
    first_sizes = []

    for g in groups:
        seq, sizes, moves = [], [], []
        for line in g["shots"]:
            total_shots += 1
            size, angle, move = shot_profile(line)
            if size:
                size_hist[size] += 1
                size_sample.setdefault(size, [])
                if len(size_sample[size]) < 3 and g["gid"] not in size_sample[size]:
                    size_sample[size].append(g["gid"])
            else:
                no_size += 1
            if angle:
                has_angle += 1
            if move:
                has_move += 1
                move_hist[move] += 1
            if size and ("特写" in size or "close" in size.lower()):
                closeups += 1
            key = "%s + %s" % (size or "?", move or "无运镜")
            suite_hist[key] += 1
            suite_sample.setdefault(key, [])
            if len(suite_sample[key]) < 3 and g["gid"] not in suite_sample[key]:
                suite_sample[key].append(g["gid"])
            seq.append(size or "(缺景别)")
            sizes.append(size)
            moves.append(move)
        # ★ 2026-10-11：组首取值从「景别」扩为「景别＋运镜」（原来只取 sizes[0]，
        #   于是"每组首镜都在推"这类回声根本查不出来）
        first_sizes.append((g["gid"], ("%s + %s" % (sizes[0] or "?", moves[0] or "无运镜")) if sizes else "(无镜头)"))

        n = len(seq)
        issues = []
        # E 单镜组
        if n == 1:
            issues.append("单镜组" + ("（有 %d 句台词）" % g["dia"] if g["dia"] else ""))
            warns.append("%s %s 整组只有 1 个镜头%s" % ("", g["gid"], "，台词 %d 句" % g["dia"] if g["dia"] else ""))
        # F 组内连续同景别
        run = 1
        for i in range(1, n):
            run = run + 1 if seq[i] == seq[i - 1] else 1
            if run == min_run and seq[i] not in ("(无景别)",):
                issues.append("连续%d镜同景别(%s)" % (min_run, seq[i]))
                warns.append("%s 连续 %d 个镜头都是 %s" % (g["gid"], min_run, seq[i]))
        # F2 组内连续同运镜（★ 2026-10-11 新增）
        run_m = 1
        for i in range(1, n):
            run_m = run_m + 1 if moves[i] == moves[i - 1] else 1
            if run_m == min_run and moves[i]:
                warns.append("%s 连续 %d 个镜头都是 %s（组内运镜回声）" % (g["gid"], min_run, moves[i]))
        # 时长下限：每镜 ≥1.2s
        if g["dur"] and n and n * 1.2 > g["dur"] + 1e-9:
            issues.append("镜数×1.2 > 时长(%ss)" % g["dur"])
            warns.append("%s %d 镜 × 1.2s > 声明 %ss" % (g["gid"], n, g["dur"]))
        blocks.append({"gid": g["gid"], "n": n, "dur": g["dur"], "dia": g["dia"],
                       "seq": seq, "issues": issues})

    # H 相邻组首镜连续同景别
    runs, cur = [], []
    for gid, comp in first_sizes:
        if cur and cur[-1][1] == comp and comp != "(无镜头)":
            cur.append((gid, comp))
        else:
            if len(cur) >= 3:
                runs.append(cur)
            cur = [(gid, comp)]
    if len(cur) >= 3:
        runs.append(cur)
    # H / 高频套件 ★ 2026-10-11：从"信息区"升级为**告警**（原来只打印、不计退出码，等于不拦）
    # ⚠ 但**固定机位类**首镜的连排降级为信息（不告警）：固定是基线，
    #   连续用同一种固定开场 ≠ 模板化（外部评审：「固定机位多 ≠ 差」）。只对**运动类**首镜连排告警。
    for r in runs:
        mv = r[0][1].split(" + ", 1)[1] if " + " in r[0][1] else "无运镜"
        if mv in set(_FIXED_HINTS):
            continue
        warns.append("相邻组首镜连续 %d 组同「景别＋运镜」：%s（首镜均为 %s）"
                     % (len(r), "、".join(gid for gid, _ in r), r[0][1]))
    # ★ 2026-10-11 修：原来这里另写了一份只有英文的集合，**漏了中文「固定 / 机位固定 / 定机位」**
    #   ⇒ 中文稿子里"固定"被当成**运动运镜**：高频套件误报、固定机位占比恒为 0。
    #   现在**唯一来源 = `_FIXED_HINTS`**（中文＋英文），⛔ 不再各自维护。
    STATIC_MOVES = set(_FIXED_HINTS)
    shots_safe = max(total_shots, 1)
    for k, c in suite_hist.most_common(20):
        # ★ 双条件：既要有数量、又要占全季镜数 ≥ suite_pct% —— 避免"小项目漏报 / 大项目误报"
        #   （只报"运动类运镜"的高频套件：固定机位是基线，重复不构成回声）
        if c < suite_min or c * 100.0 / shots_safe < suite_pct:
            continue
        mv = k.split(" + ", 1)[1] if " + " in k else "无运镜"
        if mv in STATIC_MOVES:
            continue
        sample = suite_sample.get(k, [])
        warns.append("高频构图套件「%s」出现 %d 次（占全季 %.0f%%，组：%s%s）"
                     % (k, c, c * 100.0 / shots_safe, "、".join(sample[:3]), " 等" if c > 3 else ""))

    total = max(total_shots, 1)
    # ★ 分布指标（2026-10-11 补）：固定机位是**基线**，不是例外；但**不设配额** ——
    #   配额会把"微推表现不安"这类正当用法一起禁掉（见 references/镜头语言.md §三）。
    static_shots = sum(c for k, c in move_hist.items() if k in STATIC_MOVES)
    moves_only = [(k, c) for k, c in move_hist.items() if k not in STATIC_MOVES]
    top_move, top_n = max(moves_only, key=lambda x: x[1]) if moves_only else ("", 0)
    # 极宽安全网（**告警、不阻断**）：只抓"整篇一镜未停"这种极端，不做比例门槛
    if len(groups) >= 10 and static_shots == 0 and has_move > 0:
        warns.append("全篇 %d 组**没有一个固定机位镜头**（固定是基线，不是例外）"
                     "—— 疑似每镜都在动、平均用力；对照 `镜头语言.md` §三 / §五 复核" % len(groups))

    return {
        "total_shots": total_shots, "total_groups": len(groups),
        "static_shots": static_shots, "static_pct": static_shots * 100.0 / total,
        "top_move": top_move, "top_move_n": top_n, "top_move_pct": top_n * 100.0 / total,
        "size_hist": [(k, c, size_sample.get(k, [])) for k, c in size_hist.most_common()],
        "move_hist": move_hist.most_common(15),
        "suite_hist": [(k, c, suite_sample.get(k, [])) for k, c in suite_hist.most_common(20) if c >= 1],
        "no_size": no_size, "has_angle": has_angle, "has_move": has_move, "closeups": closeups,
        "move_pct": has_move * 100.0 / total, "angle_pct": has_angle * 100.0 / total,
        "size_pct": (total - no_size) * 100.0 / total,
        "blocks": blocks, "warns": warns, "runs": runs,
    }


def render(res, src, args) -> str:
    L = ["=" * 78, "镜头语言体检 ｜ 来源：%s" % src,
         "规模：%d 组 / %d 镜" % (res["total_groups"], res["total_shots"]), "=" * 78, ""]
    for b in res["blocks"]:
        tag = ("  <== " + " / ".join(b["issues"])) if b["issues"] else ""
        dur = "%ss" % b["dur"] if b["dur"] else "--"
        print_n = " ".join(b["seq"])
        L.append("  %-12s %2d镜 %-5s %s%s" % (b["gid"], b["n"], dur, print_n, tag))
    L += ["", "-" * 78, "【核心指标】",
          "  写景别的镜头      %d/%d = %.0f%%   %s" % (res["total_shots"] - res["no_size"], res["total_shots"],
                                                        res["size_pct"], "✓" if res["no_size"] == 0 else "✗ 有镜头没写景别"),
          "  写机位/角度的镜头  %d/%d = %.0f%%" % (res["has_angle"], res["total_shots"], res["angle_pct"]),
          "  写运镜/机位行为的  %d/%d = %.0f%%   （**参考值**，⛔ 不作门 —— 是否写全是 A2 的事，是否运动是创作自由）"
          % (res["has_move"], res["total_shots"], res["move_pct"]),
          "  未写机位/角度的    %d 个                （建议补：只写景别，模型会自选且整组不换；不阻断）"
          % (res["total_shots"] - res["has_angle"]),
          "  特写镜头           %d 处            （全篇参考 5–10 处）" % res["closeups"],
          "  固定机位镜头       %d/%d = %.0f%%   （固定是**基线**，不是例外；⛔ 不设配额）"
          % (res["static_shots"], res["total_shots"], res["static_pct"]),
          "  最高频运动运镜     %s %d/%d = %.0f%%   （运镜是戏剧任务的函数，见 镜头语言.md §三）"
          % (res["top_move"] or "—", res["top_move_n"], res["total_shots"], res["top_move_pct"]), ""]
    L.append("【景别分布】")
    if res["size_hist"]:
        for k, c, smp in res["size_hist"]:
            L.append("  %-14s %4d  (%.0f%%)   组：%s" % (k, c, c * 100.0 / max(res["total_shots"], 1), "、".join(smp)))
    else:
        L.append("  （一个景别词都没识别到）")
    L.append("")
    L.append("【运镜分布（前 15）】")
    if res["move_hist"]:
        for k, c in res["move_hist"]:
            L.append("  %-20s %4d" % (k, c))
    else:
        L.append("  （未识别到任何运镜词 —— 全篇可能都是固定机位，对照避坑条 2）")
    L.append("")
    L.append("【高频构图套件（景别+运镜，≥%d 次）】" % args.suite_min)
    hits = [(k, c, s) for k, c, s in res["suite_hist"] if c >= args.suite_min]
    if hits:
        for k, c, smp in hits:
            L.append("  %-30s ×%d   组：%s" % (k, c, "、".join(smp)))
    else:
        L.append("  （无超阈值套件）")
    L.append("")
    L.append("【相邻组首镜连续同景别（≥3 组）】")
    if res["runs"]:
        for r in res["runs"]:
            L.append("  %s   （首镜均为 %s）" % ("、".join(g for g, _ in r), r[0][1]))
    else:
        L.append("  （无）")
    L.append("")
    L.append("【告警汇总】%d 处" % len(res["warns"]) if res["warns"] else "✓ 无告警")
    for w in res["warns"]:
        L.append("  ! " + w.strip())
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="跨渠道镜头语言体检门（零计费）")
    ap.add_argument("target", help="提示词文件（第N集.txt）或提示词目录")
    ap.add_argument("--min-run", type=int, default=3, help="组内连续同景别告警阈值（默认 3）")
    ap.add_argument("--min-move-pct", type=int, default=60,
                    help="【仅参考】运镜/机位行为覆盖率；2026-10-11 起**不再是门**（配额式判据 C 已删）")
    ap.add_argument("--suite-min", type=int, default=4, help="高频构图套件入榜阈值（默认 4）")
    ap.add_argument("--suite-pct", type=float, default=15.0,
                    help="高频构图套件告警的占比门槛%%（占全季镜数，默认 15；与 --suite-min 是 AND 关系）")
    ap.add_argument("--strict", action="store_true",
                    help="告警也计入退出码（rc=1）。默认**不**计入 —— 告警是「给人看的证据」，不是硬阻断")
    ap.add_argument("--json", dest="json_out", default="", help="输出机器可读结果")
    args = ap.parse_args()

    files = (sorted(glob.glob(os.path.join(args.target, "第*.txt")))
             or sorted(glob.glob(os.path.join(args.target, "*.txt")))
             if os.path.isdir(args.target) else [args.target])
    if not files or not any(os.path.exists(p) for p in files):
        print("没找到提示词文件：%s" % args.target)
        # ★ 2026-10-11 修：原来这里也 `return 2`，**与「有阻断」撞码** ⇒
        #   调用方分不清"输入错了"和"稿子要返工"。输入类错误统一 = **3**。
        return 3

    all_groups = []
    for path in files:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
        gs, fmt = parse_text(text)
        for g in gs:
            g["file"] = os.path.basename(path)
        all_groups += gs

    if not all_groups:
        print("没有解析到任何分镜组（文件不是 H3 也不是即梦提示词形态？）：%s" % args.target)
        return 3

    res = analyze(all_groups, args.min_run, args.suite_min, args.suite_pct)
    blocked = []
    # 判据 A：每镜必须写景别（规范《镜头单元与△使用规则》§六-1）
    if res["no_size"]:
        blocked.append("有 %d 个镜头没写景别（判据 A）" % res["no_size"])
    # 判据 A2（2026-10-11 新增，**替代原判据 C**）：每镜必须写**运镜状态** ——
    #   「固定」也要显式写；⛔「不写运镜」≠「固定机位」，模型会随机漂（沉默不是指令）。
    #   ★ 原判据 C（运镜覆盖率 ≥60%）已**删除**：它表面是"下限门"，本质是**创作配额** ——
    #     若一部戏本就该全用固定机位，它永远过不了门。覆盖率降级为"仅报告的参考值"。
    no_move = res["total_shots"] - res["has_move"]
    if no_move:
        blocked.append("有 %d 个镜头**没写运镜状态**（既无运动词也无固定词，判据 A2）"
                       "—— 固定也要写 `static camera` / 机位固定" % no_move)

    print(render(res, args.target, args))
    if blocked:
        print("")
        print("阻断：")
        for b in blocked:
            print("  ✗ " + b)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({"status": "OK", "groups": res["total_groups"], "shots": res["total_shots"],
                       "size_pct": res["size_pct"], "angle_pct": res["angle_pct"],
                       "move_pct": res["move_pct"], "closeups": res["closeups"],
                       "size_hist": [(k, c) for k, c, _ in res["size_hist"]],
                       "suites": [(k, c) for k, c, _ in res["suite_hist"] if c >= args.suite_min],
                       "runs": [[g for g, _ in r] for r in res["runs"]],
                       "warns": res["warns"], "blocked": blocked}, fh,
                      ensure_ascii=False, indent=2)
        print("\njson -> %s" % args.json_out)

    # ★ 2026-10-11 三态退出码（外部评审结论：若 CI / 生成脚本把非零退出码都当失败，
    #   "告警"实际就变成了硬阻断 —— 与"呈现证据、由人判断"的意图冲突）：
    #   0 = PASS（无阻断，告警只呈现）｜ 2 = BLOCK（结构性错误）｜ 1 = 仅 --strict 下
    if blocked:
        return 2
    if args.strict and res["warns"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
