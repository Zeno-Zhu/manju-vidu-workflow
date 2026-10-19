# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:8ef2898464 · 溯源标记，请保留
"""rewrite_shots_jimeng.py —— 对**即梦系**分镜组正文做「只换镜头行」的确定性改写（★ 渠道无关）。

★ 2026-10-11 上浮自 `manju-vidu/scripts/vidu_rewrite_shots.py`：
它处理的是**即梦系通用语法**（`分镜组ID：` ＋ `△` 镜头行 —— 云鲸 / 器灵 / libtv / 迪拜 / 锦行 **共用**），
**不是 vidu 专属能力** ⇒ 迁入主线 `tools/`；所有路径由调用方**显式传入**（⛔ 不硬编码任何项目）。
⚠ H3 语法（`[Shot N]` ＋ 六字段）不适用本工具。

为什么要有这个脚本
------------------
`第1集.txt` 里的台词行与它下一行的 `<>` 表演指示受「台词零容错」红线保护，
任何改写都必须证明**台词一个字都没动**。人工在 2233 行文件里改 △ 行极易连带改到台词，
所以把改写做成**补丁 + 应用器**：

    _镜头改写_v2.json   { 组ID: { segs: [ {shot: "△…", dia: [台词索引…]} ] } }
    ↓ 应用器（本脚本）
    第1集.txt（就地重写，先备份）

`dia` 用**原始台词索引**指认归属，应用器强制断言：
  · 每组所有 seg.dia 拼起来恰好等于 [0,1,…,n-1]（不缺、不重、不换序）
  · 重写前后全篇台词的**多重集完全一致**
任一条不成立 → 整个文件不落盘。

用法
----
    python vidu_rewrite_shots.py --patch <patch.json> [--src <txt>] [--apply]
    # 不带 --apply 只做校验 + 输出指标报告（dry-run，不写文件）

产出指标（同时是质量门）：
  组数 / △总数 / 平均每△台词数 / 单△组数 / 含运镜的△占比 / 特写与近景数 / 每组 n_shots*1.2 ≤ 时长
"""
import json, re, os, sys, io, shutil, argparse, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ★ 2026-10-11 上浮：路径一律由调用方显式传入，⛔ 不再硬编码项目
DEF_SRC = None
DEF_PATCH = None

DIA = re.compile(r'^([^：<>\n]{1,12})：(.*)$')
DIRLINE = re.compile(r'^<(.+)>$')

# 运镜/机位词表（用于统计"有多少 △ 真的写了摄影机行为"）
MOVE_WORDS = ('推', '拉', '摇', '横移', '跟拍', '跟随', '跟进', '跟移', '横摇', '上摇', '下压', '上抬',
              '环绕', '过肩', '越肩', '手持', '俯拍', '仰拍', '微俯', '略仰', '摇向', '推移',
              '跟手', '向后微跟', '缓推', '轻推', '下移')
SHOT_WORDS = ('特写', '近景', '中近景', '中景', '全景', '远景')


def parse_body(block):
    """把区块拆成 (head, items, tail)。

    items 里两类：
      ('shot', 文本)
      ('dia', (说话人, 台词, 指示行))
    """
    key = '内容如下：'
    i = block.find(key)
    if i < 0:
        raise ValueError('找不到「内容如下：」')
    head = block[:i + len(key)]
    rest = block[i + len(key):]
    j = rest.find('\n【本剧其他质感要求】')
    tail = rest[j:] if j >= 0 else ''
    body = rest[:j] if j >= 0 else rest

    ls = [x.rstrip() for x in body.split('\n')]
    items, k = [], 0
    # 跳过正文前的空行
    while k < len(ls) and not ls[k].strip():
        k += 1
    while k < len(ls):
        s = ls[k].strip()
        if not s:
            k += 1
            continue
        if s.startswith('△'):
            items.append(('shot', s))
            k += 1
            continue
        if s.startswith('<'):
            k += 1
            continue
        m = DIA.match(s)
        if m and k + 1 < len(ls) and ls[k + 1].strip().startswith('<'):
            items.append(('dia', (m.group(1), m.group(2), ls[k + 1].strip())))
            k += 2
            continue
        raise ValueError('无法归类的正文行: %r' % s[:40])
    return head, items, tail


def rebuild(head, dias, segs):
    """按 segs 重新拼正文：每段 = 新 △ 行 + 该段承载的台词原文。

    dias = 该组台词三元组列表（按原顺序），segs[].dia 是它的下标。
    """
    out = [head.rstrip('\n')]
    for sg in segs:
        out.append(sg['shot'])
        for idx in sg['dia']:
            sp, tx, dr = dias[idx]
            out.append('%s：%s' % (sp, tx))
            out.append(dr)
    return '\n'.join(out) + '\n'


def stat_shots(gid, segs, dur):
    n = len(segs)
    with_move = sum(1 for s in segs if any(w in s['shot'] for w in MOVE_WORDS))
    with_shot = sum(1 for s in segs if any(w in s['shot'] for w in SHOT_WORDS))
    closeup = sum(1 for s in segs if s['shot'].startswith('△特写') or '特写' in s['shot'][:14])
    return {'gid': gid, 'n': n, 'dur': dur, 'move': with_move, 'shot': with_shot,
            'closeup': closeup, 'ok_min': n * 1.2 <= dur + 1e-9}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True, help='待改写的提示词文件（即梦系语法：分镜组ID + △ 行）')
    ap.add_argument('--patch', required=True, help='镜头改写补丁 json（_镜头改写_v2.json 形态）')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--out', default=None, help='--apply 时的输出路径，默认就地覆盖（先备份 .bak）')
    a = ap.parse_args()

    raw = open(a.src, encoding='utf-8').read()
    patch = json.load(open(a.patch, encoding='utf-8'))
    patch = {k: v for k, v in patch.items() if not k.startswith('_')}

    # 用 re.split 保留分隔，逐块处理
    parts = re.split(r'(&&start\n.*?&&end)', raw, flags=re.S)
    err, report, old_all, new_all = [], [], [], []
    n_groups = 0

    for pi, blk in enumerate(parts):
        if not blk.startswith('&&start'):
            continue
        inner = blk[len('&&start\n'):-len('&&end')]
        gid = re.search(r'分镜组ID：(\S+)', inner).group(1)
        dur = int(re.search(r'分镜组时长：(\d+)s', inner).group(1))
        n_groups += 1

        head, items, tail = parse_body(inner)
        dias = [it[1] for it in items if it[0] == 'dia']
        old_all += [d[1] for d in dias]

        if gid not in patch:
            # 不给补丁的组原样保留（但仍要计入台词）
            new_all += [d[1] for d in dias]
            continue
        segs = patch[gid]['segs']

        # ---- 强断言 1：dia 索引恰好覆盖 0..n-1 ----
        flat = [i for sg in segs for i in sg['dia']]
        if flat != list(range(len(dias))):
            err.append('[%s] dia 索引未完整顺序覆盖：给 %s / 该组台词 %d 条'
                       % (gid, flat, len(dias)))
            new_all += [d[1] for d in dias]
            continue

        new_all += [dias[i][1] for i in flat]

        # ---- 强断言 2：每段必须有 △ ----
        for si, sg in enumerate(segs):
            if not sg['shot'].startswith('△'):
                err.append('[%s] seg%d 不是 △ 开头' % (gid, si))
            if '《' in sg['shot'] or '》' in sg['shot']:
                err.append('[%s] seg%d 出现占位符《》' % (gid, si))

        st = stat_shots(gid, segs, dur)
        report.append(st)
        body = rebuild(head, dias, segs)
        parts[pi] = '&&start\n' + body + tail + '&&end'

    # ---- 强断言 3：全篇台词多重集一致 ----
    co, cn = collections.Counter(old_all), collections.Counter(new_all)
    if co != cn:
        for k in set(list(co) + list(cn)):
            if co[k] != cn[k]:
                err.append('台词不匹配（旧%dx/新%dx）: %s' % (co[k], cn[k], k[:30]))

    # ---------- 报告 ----------
    n_shot_tot = sum(r['n'] for r in report)
    n_move = sum(r['move'] for r in report)
    n_shotw = sum(r['shot'] for r in report)
    n_close = sum(r['closeup'] for r in report)
    n_dia = sum(len(v['segs']) and sum(len(s['dia']) for s in v['segs'])
                for k, v in patch.items())
    print('覆盖组 %d / 全篇组 %d' % (len(report), n_groups))
    print('台词 旧 %d 条 / 新 %d 条 ｜ 多重集%s'
          % (len(old_all), len(new_all), '一致 ✅' if co == cn else '不一致 ❌'))
    print('△ 总数 %d ｜ 平均每 △ 台词 %.2f 条' % (n_shot_tot, n_dia / max(n_shot_tot, 1)))
    print('写了运镜/机位行为的 △：%d / %d = %.0f%%'
          % (n_move, n_shot_tot, 100.0 * n_move / max(n_shot_tot, 1)))
    print('带明确景别的 △：%d / %d = %.0f%%'
          % (n_shotw, n_shot_tot, 100.0 * n_shotw / max(n_shot_tot, 1)))
    print('特写镜头：%d 处' % n_close)
    bad = [r for r in report if not r['ok_min']]
    print('n_shots×1.2 ≤ 时长的组：%d / %d%s'
          % (len(report) - len(bad), len(report),
             '' if not bad else '  ❌ 超限: ' + ', '.join('%s(%d△/%ds)' % (r['gid'], r['n'], r['dur']) for r in bad)))
    print()
    print('  %-10s △  时长  运镜  景别  特写' % '组')
    for r in report:
        print('  %-10s %-3d %-5d %-5d %-5d %d' % (r['gid'], r['n'], r['dur'], r['move'], r['shot'], r['closeup']))

    if err:
        print()
        print('❌ 错误 %d 条（未落盘）：' % len(err))
        for e in err[:40]:
            print('   -', e)
        return 1

    if not a.apply:
        print()
        print('✅ 校验通过（dry-run，未写文件）。加 --apply 落盘。')
        return 0

    out = a.out or a.src
    if out == a.src:
        bak = a.src + '.bak-before-shots'
        if not os.path.exists(bak):
            shutil.copyfile(a.src, bak)
            print('已备份 →', bak)
    open(out, 'w', encoding='utf-8').write(''.join(parts))
    print('✅ 已写入', out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
