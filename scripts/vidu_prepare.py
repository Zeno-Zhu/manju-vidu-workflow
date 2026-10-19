# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:dffbefd942 · 溯源标记，请保留
"""
vidu_prepare.py —— 由「项目已有的作业单 + 资产库 + 音色库」生成 Vidu 渠道的作业参数文件。

输入（显式传路径）：
  --jobs      云鲸作业单 json（含 id / prompt / refs / ratio）
  --spec      分镜组规格 json（含 need / 说话人）
  --asset-dir 项目图片资产目录
  --panel-dir 技能面板目录（refs 里的「技能」类资产在这里）
  --voice-index 音色索引 json
  --voice-dir   音色 wav 目录
  --voice-map   角色→音色 映射 json（可选；不给则用内置默认表）
  --out       输出参数文件

★ 只做「解析 + 映射 + 落参数」，不碰浏览器。
"""
from __future__ import annotations

import argparse
import json
import os
import re

# 默认角色→音色映射（可被 --voice-map 覆盖）
# 依据：剧本角色画像（性别/年龄/性格） ⟷ 音色库 recommended_roles / personality
DEFAULT_VOICE_MAP = {
    "二丫":   {"voice_id": "VOC_F_YOUNG_PLAIN_01",  "why": "1993年18-20岁农村姑娘，嘴硬心软；「女主/年轻妻子/女儿」档"},
    "周国强": {"voice_id": "VOC_C_BOY_10_14_01",    "why": "1993年14-16岁少年，饿怕了、惊惶；「青春期儿子/护着妈妈的男孩」档"},
    "王叔":   {"voice_id": "VOC_M_MID_STRONG_01",   "why": "90年代农村中年男，追打喊叫、声冲；「严父/训人者」档"},
    "村民":   {"voice_id": "VOC_M_MID_PLAIN_02",    "why": "画外追赶声，急而音量低；中年男自然02"},
    "秀兰":   {"voice_id": "VOC_F_OLDER_WARM_01",   "why": "50多岁独居母亲，爽快心善；「慈祥母亲/善良的婆婆」档"},
    "周叔":   {"voice_id": "VOC_M_OLDER_STEADY_01", "why": "50多岁体面温和、见过世面；「沉稳厚重/有地位的长辈」档"},
    "晓敏":   {"voice_id": "VOC_F_YOUNG_DIRECT_01", "why": "30岁城市女儿，说话直接；「职场女性/直接利落」档"},
    "周凯":   {"voice_id": "VOC_M_YOUNG_STEADY_01", "why": "30岁护着父亲的儿子，有火气但靠谱；「沉稳可靠/年轻父亲」档"},
    "母亲":   {"voice_id": "VOC_F_ELDER_PLAIN_01",  "why": "画外一声，年长女；「奶奶/老太太」档"},
}

DURATION_MIN, DURATION_MAX = 3, 16  # 页面实测档位：3..16


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def pick_images(refs, asset_dir, panel_dir):
    """把 refs 里的资产名解析成本地图片路径（先查资产目录，再查技能面板）。"""
    out, miss = [], []
    for r in refs:
        hit = None
        for d in (asset_dir, panel_dir):
            if not d:
                continue
            for ext in (".png", ".jpg", ".jpeg", ".webp"):
                p = os.path.join(d, r + ext)
                if os.path.exists(p):
                    hit = p
                    break
            if hit:
                break
        if hit:
            out.append({"name": r, "path": hit.replace("\\", "/")})
        else:
            miss.append(r)
    return out, miss


def build_duration_map(spec):
    """组号 -> need（内容需求量，秒）。"""
    m = {}
    for g in spec.get("groups", []):
        gid = g.get("group_id", "")
        base = gid[:-3] if gid.endswith("_H3") else gid  # 1_1_H3 -> 1_1
        m[base] = g.get("need")
    return m


def map_duration(need):
    """need(内容需求量) -> Vidu 档位。超上限说明会截断，如实标注。"""
    if need is None:
        return DURATION_MAX, "无 need 数据，取最大档"
    want = int(need + 0.999)
    if want > DURATION_MAX:
        return DURATION_MAX, "need=%.2fs 超档位上限 %ds，取最大档（内容会被压缩/截断）" % (need, DURATION_MAX)
    if want < DURATION_MIN:
        return DURATION_MIN, "need=%.2fs 低于下限，取最小档" % need
    return want, "need=%.2fs 就近取档" % need


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--asset-dir", required=True)
    ap.add_argument("--panel-dir", default="")
    ap.add_argument("--voice-index", required=True)
    ap.add_argument("--voice-dir", required=True)
    ap.add_argument("--voice-map", default="")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 组（0=全部）")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    jobs_src = load_json(a.jobs)
    spec = load_json(a.spec)
    vmap = load_json(a.voice_map)["map"] if a.voice_map else DEFAULT_VOICE_MAP
    vindex = {v["voice_id"]: v for v in load_json(a.voice_index)["voices"]}

    needs = build_duration_map(spec)
    speakers_by_group = {}
    for g in spec.get("groups", []):
        gid = g.get("group_id", "")
        base = gid[:-3] if gid.endswith("_H3") else gid
        sp = []
        for it in g.get("items", []):
            if it.get("type") == "dialogue" and it.get("speaker") and it["speaker"] not in sp:
                sp.append(it["speaker"])
        speakers_by_group[base] = sp

    out_jobs, all_miss = [], []
    for j in jobs_src["jobs"]:
        jid = j["id"]
        base = re.sub(r"_[^_]+$", "", jid)  # 1_1_标准 -> 1_1
        imgs, miss = pick_images(j.get("refs", []), a.asset_dir, a.panel_dir)
        all_miss += miss
        dur, why = map_duration(needs.get(base))
        voices = []
        for sp in speakers_by_group.get(base, []):
            ent = vmap.get(sp)
            if not ent:
                continue
            vid = ent["voice_id"] if isinstance(ent, dict) else ent
            vp = os.path.join(a.voice_dir, vid + "_neutral.wav")
            voices.append({
                "speaker": sp,
                "voice_id": vid,
                "path": vp.replace("\\", "/"),
                "exists": os.path.exists(vp),
                "why": (ent.get("why") if isinstance(ent, dict) else ""),
                "display_name": vindex.get(vid, {}).get("display_name", ""),
            })
        out_jobs.append({
            "id": base,
            "src_id": jid,
            "duration": dur,
            "duration_reason": why,
            "need": needs.get(base),
            "ratio": j.get("ratio", "9:16"),
            "images": imgs,
            "n_images": len(imgs),
            "voices": voices,
            "n_voices": len(voices),
            "prompt": j["prompt"],
            "prompt_chars": len(j["prompt"]),
        })

    if a.limit:
        out_jobs = out_jobs[:a.limit]

    res = {
        "_note": "Vidu 渠道作业参数。由 _云鲸作业单.json + _云鲸分镜组规格.json 生成；"
                 "图片来自 01_资产库/ + 常用技能面板/；音色来自 asset_library_v1/assets/voices/。",
        "source_jobs": os.path.abspath(a.jobs).replace("\\", "/"),
        "model_display": "Vidu Q4 预览版",
        "defaults": {"resolution": "1080p", "codec": "H265", "ratio": "9:16", "quantity": 1},
        "duration_domain": [DURATION_MIN, DURATION_MAX],
        "totals": {
            "jobs": len(out_jobs),
            "missing_refs": sorted(set(all_miss)),
            "images_max": max([j["n_images"] for j in out_jobs], default=0),
            "voices_max": max([j["n_voices"] for j in out_jobs], default=0),
        },
        "jobs": out_jobs,
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    print("写出:", a.out)
    print("组数:", res["totals"]["jobs"], "| 图片最多:", res["totals"]["images_max"],
          "| 音色最多:", res["totals"]["voices_max"])
    if res["totals"]["missing_refs"]:
        print("!! 找不到的 refs:", res["totals"]["missing_refs"])
    print()
    print("%-8s %-4s %-34s %-6s %s" % ("组", "时长", "取档原因", "图/音", "音色"))
    for j in out_jobs:
        vs = ",".join("%s=%s" % (v["speaker"], v["voice_id"].replace("VOC_", "")) for v in j["voices"])
        print("%-8s %-4d %-34s %d/%-3d  %s" % (j["id"], j["duration"], j["duration_reason"][:32],
                                               j["n_images"], j["n_voices"], vs))


if __name__ == "__main__":
    main()
