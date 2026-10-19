# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:8d83880822 · 溯源标记，请保留
"""
vidu_make_jobs.py —— 由 _重切清单.json 生成 Vidu 提交作业单 jobs_vidu.json。

同时：
- 把每组的「角色音色」参考音从全局音色库复制到 <项目>/01_资产库/音色/<角色名>.wav
  （幂等；写 <项目>/01_资产库/音色/_音色映射.json 留痕）
- 每组 images 按 pn 顺序给出本地文件路径（Vidu：pnN ↔ 图N 同序）
- audios 按 vm 顺序给出本地文件路径（vmN ↔ 参考音频N 同序，≤3）
- prompt = 该组的 &&start…&&end 内容（去掉标记行）+ 追加「即梦20固定提示词结尾.txt」

用法：
  python vidu_make_jobs.py --project <你的项目目录>          # ★ 圈友/换项目用这个
  python vidu_make_jobs.py                                   # 不带参数 = 本机默认项目
  python vidu_make_jobs.py --no-fixed-tail --out <path>
  # 细粒度覆盖：--list / --txt / --asset-dir / --voice-dir / --panel-dir / --fixed-tail
"""
import json, os, re, shutil, sys, io, argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ★ 全部路径均可被命令行参数覆盖（见 main）；下面只是**本机默认值**，圈友用 --project 指定自己的项目。
PROJ = os.environ.get('MANJU_PROJ') or r'E:/AI视频项目/剧情电商/标准雪花膏'
WS = os.environ.get('MANJU_WS') or r'E:/AI软件/剧情电商创作'
LIST = PROJ + '/_vidu渠道/_重切清单.json'
TXT = PROJ + '/_vidu渠道/标准提示词_即梦20/第1集.txt'
VOICE_LIB = WS + '/asset_library_v1/assets/voices'
VOICE_DIR = PROJ + '/01_资产库/音色'
ASSET_DIR = PROJ + '/01_资产库'
SKILL_PANEL = WS + '/漫剧创作工作流/常用技能面板'
# 固定结尾语：★ 优先用**本包自带**的 prompts/（交付包自包含），找不到再回退到工作流目录
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_TAIL = os.path.join(os.path.dirname(_HERE), 'prompts', '即梦20固定提示词结尾.txt')
FIXED_TAIL = _PKG_TAIL if os.path.isfile(_PKG_TAIL) else WS + '/漫剧创作工作流/prompts/即梦20固定提示词结尾.txt'
OUT = PROJ + '/_vidu渠道/jobs_vidu.json'

MODEL = 'Vidu Q4 预览版'
RATIO = '9:16'
RES = '1080p'


def count_dialogue_lines(body):
    """数本组台词条数。

    判据：台词行**后面紧跟一行 `<>` 表演指示**（本剧新口径，2026-10-07 起）。
    比"短冒号前缀"稳得多 —— 不会被「内容如下：」「【分镜要求】：」这类元信息行骗到。
    """
    ls = body.split('\n')
    n = 0
    for i, ln in enumerate(ls):
        if '：' not in ln or ln.startswith(('【', '<', '△')):
            continue
        if i + 1 < len(ls) and ls[i + 1].startswith('<'):
            n += 1
    return n


def fit_tail(tail, dur, n_lines):
    """把通用结尾固定语里的秒数/条数**换算到本组时长口径**。

    ★ 为什么不在共享文件里改：`即梦20固定提示词结尾.txt` 是**多渠道共用**的，
      在源头改会波及 libtv / 即梦20。所以只在生成 Vidu 作业单时做确定性替换。

    换算规则：
      · 「须在 30 秒内均匀分布」→ 本组时长
      · 「严禁整段 8 秒以上没有人声」→ 按 30s 锚点同比缩放 sil = clamp(round(dur*8/30), 3, 8)
      · 「至少要有 3 处以上有台词的分镜」→ 本组实际台词条数（原值对 15s 组常常不可满足）
      · ★ **不动**「每一段连续的无人声表演不得超过约 5 秒」—— 那是**绝对量**（管的是"单段"），
        与全片时长无关；上一版把它一起缩成 3 秒属于过度收紧，已回退。
    """
    sil = max(3, min(8, int(round(dur * 8.0 / 30.0))))
    n = max(1, n_lines)
    t = tail
    for a, b in (
        # ★ 用户 2026-10-07 点名要求移除：把运镜从"交给模型自由发挥"收回正文显式指定。
        #   注意只删这一句，保留后面的「根据剧情合理规划分镜…」等全部约束。
        ('自由合理运镜，', ''),
        ('自由运镜，', ''),
        ('须在 30 秒内均匀分布', '须在 %d 秒内均匀分布' % dur),
        ('须在30秒内均匀分布', '须在%d秒内均匀分布' % dur),
        ('严禁出现整段 8 秒以上没有任何人声的情况',
         '严禁出现整段超过 %d 秒没有任何人声的情况' % sil),
        ('且全片至少要有 3 处以上有台词的分镜',
         '且全片台词分镜不少于 %d 处' % n),
    ):
        t = t.replace(a, b)
    return t


def main():
    global LIST, TXT, VOICE_DIR, ASSET_DIR, SKILL_PANEL, FIXED_TAIL, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default=None, help='项目根目录（默认取本机默认值）')
    ap.add_argument('--list', default=None, help='_重切清单.json（默认 <项目>/_vidu渠道/_重切清单.json）')
    ap.add_argument('--txt', default=None, help='重切后的提示词（默认 <项目>/_vidu渠道/标准提示词_即梦20/第1集.txt）')
    ap.add_argument('--asset-dir', default=None, help='资产图目录（默认 <项目>/01_资产库）')
    ap.add_argument('--voice-dir', default=None, help='音色落地目录（默认 <项目>/01_资产库/音色）')
    ap.add_argument('--panel-dir', default=None, help='技能面板目录（refs 里的「技能」类资产）')
    ap.add_argument('--fixed-tail', default=None, help='固定结尾语文件（默认取本包 prompts/ 下那份）')
    ap.add_argument('--no-fixed-tail', action='store_true')
    ap.add_argument('--out', default=None, help='输出 jobs_vidu.json（默认 <项目>/_vidu渠道/jobs_vidu.json）')
    a = ap.parse_args()

    if a.project:
        LIST = a.project + '/_vidu渠道/_重切清单.json'
        TXT = a.project + '/_vidu渠道/标准提示词_即梦20/第1集.txt'
        ASSET_DIR = a.project + '/01_资产库'
        VOICE_DIR = a.project + '/01_资产库/音色'
        OUT = a.project + '/_vidu渠道/jobs_vidu.json'
    if a.list:       LIST = a.list
    if a.txt:        TXT = a.txt
    if a.asset_dir:  ASSET_DIR = a.asset_dir
    if a.voice_dir:  VOICE_DIR = a.voice_dir
    if a.panel_dir:  SKILL_PANEL = a.panel_dir
    if a.fixed_tail: FIXED_TAIL = a.fixed_tail
    if a.out:        OUT = a.out

    for label, p in (('重切清单', LIST), ('提示词', TXT)):
        if not os.path.isfile(p):
            sys.exit('[ERROR] %s不存在：%s\n        用 --project 或 --list/--txt 指定你自己的路径。' % (label, p))
    if not a.no_fixed_tail and not os.path.isfile(FIXED_TAIL):
        sys.exit('[ERROR] 固定结尾语不存在：%s\n        用 --fixed-tail 指定，或加 --no-fixed-tail 不拼结尾。' % FIXED_TAIL)

    spec = json.load(open(LIST, encoding='utf-8'))
    raw = open(TXT, encoding='utf-8').read()
    blocks = {re.search(r'分镜组ID：(\S+)', b).group(1): b.strip()
              for b in re.findall(r'&&start\n(.*?)&&end', raw, re.S)}
    tail = '' if a.no_fixed_tail else open(FIXED_TAIL, encoding='utf-8').read().strip()

    os.makedirs(VOICE_DIR, exist_ok=True)
    vmap = {}
    mpath = VOICE_DIR + '/_音色映射.json'
    if os.path.exists(mpath):
        vmap = json.load(open(mpath, encoding='utf-8'))

    jobs, missing = [], []
    for g in spec['groups']:
        gid = g['gid']
        imgs = []
        for n in (g['人物视觉资产'] + g['场景资产'] + g['道具资产']):
            p = '%s/%s.png' % (ASSET_DIR, n)
            if not os.path.exists(p):
                missing.append((gid, 'img', n, p)); continue
            imgs.append({'name': n, 'path': p})
        for n in g['技能参考']:
            p = '%s/%s.png' % (SKILL_PANEL, n)
            if not os.path.exists(p):
                missing.append((gid, 'img', n, p)); continue
            imgs.append({'name': n, 'path': p})

        auds = []
        for v in g['音色']:
            if not v.get('path'):
                missing.append((gid, 'voice-none', v['speaker'], '')); continue
            dst = '%s/%s.wav' % (VOICE_DIR, v['speaker'])
            if not os.path.exists(dst):
                shutil.copyfile(v['path'], dst)
            vmap[v['speaker']] = {'voice_id': v['vid'], 'display': v['display'],
                                  'src': v['path'], 'file': dst}
            auds.append({'speaker': v['speaker'], 'name': v['display'], 'path': dst})

        body = blocks[gid]
        n_lines = count_dialogue_lines(body)
        prompt = body
        if tail:
            prompt = body + '\n\n' + fit_tail(tail, g['duration'], n_lines)
        jobs.append({
            'id': gid, 'src_id': g['parent'],
            'duration': g['duration'], 'need': g['need'],
            'ratio': g['ratio'] if 'ratio' in g else RATIO,
            'model': MODEL, 'resolution': RES,
            'n_lines': n_lines,
            'n_images': len(imgs), 'n_audios': len(auds),
            'images': imgs, 'audios': auds,
            'prompt': prompt, 'prompt_chars': len(prompt),
        })

    json.dump(vmap, open(mpath, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    out = {
        '_note': 'Vidu Q4 渠道作业参数（即梦20 口径 ≤15s）。由 _重切清单.json 生成；'
                 'prompt = 分镜组正文 + 即梦20固定提示词结尾（★ 结尾内秒数/条数已按本组时长换算，'
                 '不改共享文件）。pnN↔images[N-1]，vmN↔audios[N-1]。',
        'source': LIST,
        'model_display': MODEL,
        'defaults': {'resolution': RES, 'ratio': RATIO, 'quantity': 1},
        'duration_domain': [3, 16],
        'constraints': {'max_images': 9, 'max_audios': 3, 'max_declared_seconds': 15,
                        'fixed_tail': not a.no_fixed_tail,
                        'tail_fitted': not a.no_fixed_tail},
        'totals': {'jobs': len(jobs),
                   'images_max': max(j['n_images'] for j in jobs),
                   'audios_max': max(j['n_audios'] for j in jobs),
                   'seconds': sum(j['duration'] for j in jobs),
                   'dialogue_lines': sum(j['n_lines'] for j in jobs),
                   'missing_refs': missing},
        'jobs': jobs,
    }
    json.dump(out, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print('组数 %d ｜ 图片最多 %d（≤9）｜ 音色最多 %d（≤3）｜ 声明总时长 %ds ｜ 台词 %d 条'
          % (len(jobs), out['totals']['images_max'], out['totals']['audios_max'],
             out['totals']['seconds'], out['totals']['dialogue_lines']))
    print('prompt 字数 min/max: %d / %d'
          % (min(j['prompt_chars'] for j in jobs), max(j['prompt_chars'] for j in jobs)))
    if missing:
        print('⚠️ 缺失引用 %d 条：' % len(missing))
        for m in missing[:20]:
            print('   ', m)
    else:
        print('✅ 引用齐备')
    print('已写', a.out)


if __name__ == '__main__':
    main()
