# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:e06e8da233 · 溯源标记，请保留
"""
vidu_check_prompts.py —— 对「即梦20（Vidu Q4）」重写结果跑确定性校验门。

校验项：
 A 结构：组数 / ID 唯一 / ID 格式 / 每组字段齐全
 B 版权：&start/&end 配对、一级标题唯一
 C 时长：档位 ∈ {5,8,10,12,15} 且 声明时长 ≥ 该组实际 need（汉字÷3.9 + 段开销）
 D 资产：pn 连续且 ≤9、资产名必须在资产库存在；vm ≤3 且与"实际发言角色"一致
 E 音色：每组最多 3 名「有独立人物资产」的角色发言
 F 台词：新旧台词逐字多重集完全一致（台词逐字保留红线）
 G 格式：所有 <> 紧跟在"角色名：台词"之后，且以「保持<角色名>的音色不变，」开头
 H 正文：每组正文第一行必须是 △；正文不得出现《》占位与未替换变量
 I 规范：未出现内部工作痕迹（主镜头锚点/沿用上一组站位）；未出现禁用的音色重塑词

用法：
  python vidu_check_prompts.py --project <你的项目目录>            # ★ 圈友/换项目用这个
  python vidu_check_prompts.py --new <新稿> --old <旧稿> --asset-dir <资产图目录>
  python vidu_check_prompts.py --project <...> --panel-dir <技能面板目录> --strict
  （不给 --project 时用本机默认项目；不给 --asset-dir/--panel-dir 则跳过对应存在性检查）
"""
import json, re, os, sys, io, collections, argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ★ 全部路径均可被命令行参数覆盖（见 main）；下面只是**本机默认值**，圈友用 --project/--new 指定。
PROJ = os.environ.get('MANJU_PROJ') or r'E:/AI视频项目/剧情电商/标准雪花膏'
OLD = PROJ + '/00_剧本与提示词/标准提示词/第1集.txt'
NEW = PROJ + '/_vidu渠道/标准提示词_即梦20/第1集.txt'
ASSET_DIR = PROJ + '/01_资产库'
SKILL_PANEL = os.environ.get('MANJU_WS', r'E:/AI软件/剧情电商创作') + '/漫剧创作工作流/常用技能面板'

TIERS = [5, 8, 10, 12, 15]
RATE, GAP, HT = 3.9, 0.30, 0.15
CJK = re.compile(r'[\u4e00-\u9fff]')
DIA = re.compile(r'^([^：<>\n]{1,12})：(.*)$')
DIRLINE = re.compile(r'^<(.+)>$')
FORCE = ('【强制声明】无背景音乐；画面无字幕/台词文字/水印/Logo/角标/UI；声音：不要配乐，仅保留同期声；'
         '全程无背景音乐，仅保留真实环境音效与动作同期声。')

REQUIRED = ['【基础信息】', '【强制声明】', '【统一风格模块】', '【资产盘点】', '【技能参考调用说明】',
            '【本提示词基础信息】', '【本提示词正文信息】']
REQ_FIELDS = ['分镜组ID：', '分镜组时长：', '大词层：', '小词层：', '故事背景：', '镜头主任务：',
              '视觉叙事落点：', '关键情绪和冲突：', '环境时空基准：']
BAD_WORDS = ['主镜头锚点', '沿用上一组站位', '磁性嗓音', '少女音', '御姐音', '烟嗓', '播音腔']


def cjk(s):
    return len(CJK.findall(s))


def need_of(lines):
    chars = sum(cjk(DIA.match(l).group(2)) for l in lines if DIA.match(l) and not l.startswith('△'))
    segs = sum(1 for l in lines if DIA.match(l) and not l.startswith('△'))
    return chars / RATE + max(segs - 1, 0) * GAP + segs * HT, chars, segs


def blocks(path):
    raw = open(path, encoding='utf-8').read()
    return raw, re.findall(r'&&start\n(.*?)&&end', raw, re.S)


def main():
    global OLD, NEW, ASSET_DIR, SKILL_PANEL
    ap = argparse.ArgumentParser(description='即梦20（Vidu Q4）重写提示词的确定性校验门 A–I')
    ap.add_argument('--project', default=None, help='项目根目录（据它推导 old/new/asset 三个默认路径）')
    ap.add_argument('--old', default=None, help='改写前的旧提示词（台词逐字对账的基准）')
    ap.add_argument('--new', default=None, help='要校验的新提示词')
    ap.add_argument('--asset-dir', default=None, help='资产图目录（不给则跳过「资产是否存在」检查）')
    ap.add_argument('--panel-dir', default=None, help='技能面板目录（不给则跳过「技能图是否存在」检查）')
    ap.add_argument('--strict', action='store_true')
    a = ap.parse_args()

    if a.project:
        OLD = a.project + '/00_剧本与提示词/标准提示词/第1集.txt'
        NEW = a.project + '/_vidu渠道/标准提示词_即梦20/第1集.txt'
        ASSET_DIR = a.project + '/01_资产库'
    if a.old:        OLD = a.old
    if a.new:        NEW = a.new
    if a.asset_dir:  ASSET_DIR = a.asset_dir
    if a.panel_dir:  SKILL_PANEL = a.panel_dir
    else:            SKILL_PANEL = None      # ★ 没给就不校验技能图，避免拿本机路径乱报

    for label, p in (('旧提示词', OLD), ('新提示词', NEW)):
        if not os.path.isfile(p):
            sys.exit('[ERROR] %s不存在：%s\n        用 --project 或 --old/--new 指定你自己的路径。' % (label, p))

    err, warn = [], []

    raw_old, old_blocks = blocks(OLD)
    raw_new, new_blocks = blocks(NEW)
    print('旧组 %d ｜ 新组 %d' % (len(old_blocks), len(new_blocks)))

    # ---------- A/B 结构 ----------
    if raw_new.count('&&start') != raw_new.count('&&end') != len(new_blocks):
        err.append('&&start/&&end 数量不配对')
    ids = [re.search(r'分镜组ID：(\S+)', b).group(1) for b in new_blocks]
    if len(set(ids)) != len(ids):
        dup = [k for k, v in collections.Counter(ids).items() if v > 1]
        err.append('组号重复: %s' % dup)
    for i in ids:
        if not re.match(r'^\d+_\d+_标准$', i):
            err.append('组号格式不合规: %s' % i)
    t1 = [l for l in raw_new.split('\n') if l.strip().startswith('第') and '集·' in l]
    if len(t1) != 1:
        err.append('一级标题数量异常: %d' % len(t1))

    assets_on_disk = (set(os.path.splitext(f)[0] for f in os.listdir(ASSET_DIR))
                      if ASSET_DIR and os.path.isdir(ASSET_DIR) else None)
    skill_on_disk = (set(os.path.splitext(f)[0] for f in os.listdir(SKILL_PANEL))
                     if SKILL_PANEL and os.path.isdir(SKILL_PANEL) else set())
    if assets_on_disk is None and not skill_on_disk:
        warn.append('未给 --asset-dir/--panel-dir，已跳过「资产是否存在」检查')

    total_decl = total_need = 0
    all_new_dia = []
    rows = []

    for b in new_blocks:
        gid = re.search(r'分镜组ID：(\S+)', b).group(1)

        def bad(msg):
            err.append('[%s] %s' % (gid, msg))

        # B 字段齐全
        for k in REQUIRED:
            if k not in b:
                bad('缺模块 %s' % k)
        for f in REQ_FIELDS:
            if f not in b:
                bad('缺字段 %s' % f)
        if FORCE not in b:
            bad('【强制声明】未原文输出')

        # C 时长
        m = re.search(r'分镜组时长：(\d+)s', b)
        if not m:
            bad('分镜组时长格式异常')
            dur = 0
        else:
            dur = int(m.group(1))
            if dur not in TIERS:
                bad('档位 %d 不在 %s' % (dur, TIERS))
        body = b.split('内容如下：', 1)[1].split('【本剧其他质感要求】', 1)[0]
        lines = [l.strip() for l in body.split('\n') if l.strip()]
        if not lines:
            bad('正文为空')
            continue
        nd, chars, segs = need_of(lines)
        if dur and nd > dur + 1e-9:
            bad('need %.2fs > 声明 %ds（有截断风险）' % (nd, dur))
        total_decl += dur
        total_need += nd

        # H 正文首行 △
        if not lines[0].startswith('△'):
            bad('正文首行不是 △（应以镜头开场）')

        # G 台词 / <> 配对
        dia = []
        for i, l in enumerate(lines):
            if l.startswith('△') or DIRLINE.match(l):
                continue
            md = DIA.match(l)
            if not md:
                bad('台词行格式异常: %s' % l[:30])
                continue
            sp, tx = md.group(1), md.group(2)
            dia.append((sp, tx))
            if i + 1 >= len(lines) or not DIRLINE.match(lines[i + 1]):
                bad('台词后缺 <> 行: %s' % tx[:20])
                continue
            d = DIRLINE.match(lines[i + 1]).group(1)
            if not d.startswith('保持%s的音色不变，' % sp):
                bad('<> 未以「保持%s的音色不变，」开头' % sp)
            if not d.startswith('保持'):
                bad('<> 未以「保持」开头')
        all_new_dia += [x[1] for x in dia]

        # D 资产盘点
        pns = [int(x) for x in re.findall(r'pn(\d+),', b)]
        if pns and (max(pns) > 9 or sorted(pns) != list(range(1, len(pns) + 1))):
            bad('pn 编号不连续或超 9：%s' % sorted(pns))
        names = re.findall(r'pn\d+,([^=@，,]+)=@', b)
        known = (assets_on_disk or set()) | (skill_on_disk or set())
        for n in names:
            if known and n not in known:
                bad('资产不存在于磁盘：%s' % n)
        vms = re.findall(r'vm(\d+),', b)
        if vms and sorted(int(x) for x in vms) != list(range(1, len(vms) + 1)):
            bad('vm 编号不连续：%s' % vms)
        if len(vms) > 3:
            bad('音色 %d 个 > 3' % len(vms))

        # E 发言上限 & vm 一致
        spk = []
        for s, _ in dia:
            if s not in spk:
                spk.append(s)
        vm_spk = re.findall(r'用于([^）]+)）', b)
        if dia and sorted(vm_spk) != sorted([s for s in spk if s not in ('村民', '母亲')]):
            bad('vm 音色与发言角色不一致: vm=%s 发言=%s' % (vm_spk, spk))
        main = [s for s in spk if s not in ('村民', '母亲')]
        if len(main) > 3:
            bad('有独立资产的角色发言 %d 名 > 3：%s' % (len(main), main))
        if not dia and '【音色严格规范】' in b:
            bad('无台词却输出了【音色严格规范】')
        if not dia and '人物音色资产' in b:
            bad('无台词却输出了人物音色资产')

        # I 禁用词/痕迹
        for w in BAD_WORDS:
            if w in b:
                bad('出现禁用词/内部痕迹：%s' % w)
        if '《' in body or '》' in body:
            bad('正文残留《》')

        rows.append((gid, dur, round(nd, 2), chars, segs, len(pns), len(vms), lines[0][:1]))

    # F 台词逐字（旧文件：台词与 <> 同行，需剥离方向）
    OLD_DIA = re.compile(r'^([^：\n]{1,12})：(.*?)\s*<(.+)>$')
    old_dia = []
    for b in old_blocks:
        body = b.split('内容如下：', 1)[1].split('【本剧其他质感要求】', 1)[0]
        for l in [x.strip() for x in body.split('\n') if x.strip()]:
            if l.startswith('△'):
                continue
            md = OLD_DIA.match(l)
            if md:
                old_dia.append(md.group(2).strip())
            elif DIA.match(l):
                err.append('旧文件台词行无 <> 方向: %s' % l[:30])
    co, cn = collections.Counter(old_dia), collections.Counter(all_new_dia)
    if co != cn:
        for k in co:
            if co[k] != cn[k]:
                err.append('台词不匹配（旧%dx/新%dx）: %s' % (co[k], cn[k], k))
        for k in cn:
            if k not in co:
                err.append('新出现台词: %s' % k)
    print('旧台词 %d 条 / 新台词 %d 条 ｜ 汉字 %d' % (len(old_dia), len(all_new_dia), sum(cjk(x) for x in all_new_dia)))

    print('声明总时长 %ds ｜ 需求合计 %.0fs' % (total_decl, total_need))
    print()
    for r in rows:
        print('  %-9s 档=%-3d need=%-6s 字=%-4d 段=%-2d 图=%-2d 音=%-2d' % (r[:7]))

    print()
    if err:
        print('❌ 错误 %d 条：' % len(err))
        for e in err:
            print('   -', e)
    else:
        print('✅ 全部校验通过')
    if warn:
        print('⚠️ 提醒：')
        for w in warn:
            print('   -', w)
    return 1 if err else 0


if __name__ == '__main__':
    sys.exit(main())
