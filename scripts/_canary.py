# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:c726ddb74e · 溯源标记，请保留
"""_canary.py —— 溯源金丝雀（canary / 指纹标记）核心。

═══ 它解决什么 ═══
本仓库是**公开**的，谁都能 fork、clone、复制。但**原样搬走当作自己的作品**是另一回事。
本模块做一件事：**在每个源文件里埋下独一无二、又不影响功能的标记**，
让「这份代码是不是从 manju-vidu-workflow 抄来的」**可被机械验证**。

═══ 设计要点（为什么这样埋）═══
1. **确定性指纹**：`fingerprint(rel_path)` = HMAC-SHA256(密钥, 相对路径) 前 10 位。
   ⇒ 同一个文件在任何机器上算出的指纹都一样，**可复算 = 可作证**。
2. **藏在人畜无害的位置**：写成普通注释行，混在版权/编码声明那一带。
   抄的人"顺手清理"通常只删大段注释，**不会逐行核对每一行注释**。
3. **两层标记**：
   · **行级**：每个文件一行 `# <SIG>`，含指纹。
   · **文档级**：文档里夹一句**不显眼的独特措辞**（见 `DOC_NEEDLE`），
     连同措辞一起被抄走时同样可识别。
4. **可校验**：`canary_check.py <目录>` 扫描任意目录，报告金丝雀命中 ——
   既能自查（发布前确认埋点齐全），也能在**发现疑似抄袭时取证**。

⛔ **本文件不是加密、也不是授权**，它只做**溯源**。别拿它当防盗手段。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re

PRODUCT = "manju-vidu-workflow"
SIG = "MJVID"          # 标记前缀（短、好搜、不像敏感词）
VERSION = "1.0"

# 内置密钥：只用于**生成确定性指纹**（非密码学用途，公开也无妨；
# 它的作用只是让指纹"不像随机数、且跨机器可复算"）。
_KEY = b"manju-vidu-workflow/canary/20261013/zeno"

# 文档级"暗针"：一句不显眼、但措辞独特的短语。
# 它出现在 README / SKILL.md 的正文里，读起来完全正常。
DOC_NEEDLE = "同一套制式跨渠道复用时，边界只钉在两头"

CANARY_RE = re.compile(r"MJVID:([0-9a-f]{10})")
NEEDLE_RE = re.compile(re.escape(DOC_NEEDLE))

# 需要埋点的文件类型（源码 + 文档）
TARGET_EXT = (".py", ".md", ".cmd")
# 不埋的目录（第三方 / 素材）
SKIP_PARTS = ("__pycache__", ".git", "assets")
# 不埋的文件（二进制 / 本身就是标记载体）
SKIP_FILES = ("core.enc",)


def fingerprint(rel_path: str) -> str:
    """相对路径 → 10 位确定性指纹（跨机器可复算）。"""
    p = rel_path.replace("\\", "/").strip("/").lower()
    return hmac.new(_KEY, p.encode("utf-8"), hashlib.sha256).hexdigest()[:10]


def canary_line(rel_path: str, style: str = "py") -> str:
    """生成该文件的金丝雀注释行。

    ★ 措辞刻意写成"版权/来源声明"的样子 —— 这是本地所有文件本来就该有的东西，
      所以**它不突兀**，也就不容易被当成"可疑内容"专门删掉。

    ⚠★ `.cmd` 必须**纯 ASCII**（GBK 控制台下中文乱码），且注释只能用 `rem` / `::`
      —— 所以 cmd 风格单独一版，不含中文与 `·`。
    """
    fp = fingerprint(rel_path)
    tail = "%s:%s" % (SIG, fp)
    if style == "md":
        return "<!-- (c) %s · %s · 溯源标记请勿删除 -->" % (PRODUCT, tail)
    if style == "cmd":
        return "rem (c) %s - %s - provenance marker, please keep" % (PRODUCT, tail)
    return "# (c) %s · %s · 溯源标记，请保留" % (PRODUCT, tail)


def should_mark(rel_path: str) -> bool:
    rp = rel_path.replace("\\", "/")
    if any(part in rp.split("/") for part in SKIP_PARTS):
        return False
    if rp.split("/")[-1] in SKIP_FILES:
        return False
    return rp.lower().endswith(TARGET_EXT)


def find_marker(text: str):
    """返回文本里所有金丝雀指纹。"""
    return CANARY_RE.findall(text)


def find_needle(text: str) -> bool:
    """文档里是否含"暗针"。"""
    return bool(NEEDLE_RE.search(text))


def scan(root: str):
    """扫描目录，返回命中统计。

    返回 dict：
      marked    [(rel, fp)]    —— 有金丝雀的文件
      unmarked  [rel]          —— 该埋但没埋的
      needles   [rel]          —— 含"暗针"的文档
      foreign   [(rel, fp)]    —— ★ 指纹**对不上路径**的（= 从别处搬来、改了名/换了位置的强信号）
    """
    marked, unmarked, needles, foreign = [], [], [], []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_PARTS]
        for fn in fns:
            if fn in SKIP_FILES:
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root).replace("\\", "/")
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            fps = find_marker(txt)
            if fps:
                marked.append((rel, fps[0]))
                # 指纹应与"这份文件的路径"一致；不一致 ⇒ 该文件是从别处搬来的
                if fps[0] != fingerprint(rel):
                    foreign.append((rel, fps[0]))
            elif should_mark(rel):
                unmarked.append(rel)
            if rel.lower().endswith(".md") and find_needle(txt):
                needles.append(rel)
    return {"marked": marked, "unmarked": unmarked, "needles": needles, "foreign": foreign}


def apply_marks(root: str, dry=False):
    """给 root 下所有该埋的文件植入金丝雀（幂等）。返回 (改动数, 明细)。"""
    changed = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_PARTS]
        for fn in fns:
            if fn in SKIP_FILES:
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root).replace("\\", "/")
            if not should_mark(rel) or find_marker(open(p, encoding="utf-8", errors="ignore").read()):
                continue
            style = ("md" if fn.lower().endswith(".md")
                     else ("cmd" if fn.lower().endswith(".cmd") else "py"))
            line = canary_line(rel, style)
            txt = open(p, encoding="utf-8", errors="ignore").read()
            lines = txt.split("\n")
            # 插入位置：编码声明之后（py）/ 第一个标题之后（md）/ 首行（cmd）
            if style == "py":
                idx = 1 if lines and lines[0].startswith("#") else 0
                # 跳过已有的 coding 行与 docstring 起始
                while idx < len(lines) and (lines[idx].startswith("#") or not lines[idx].strip()):
                    idx += 1
                lines.insert(idx, line)
            elif style == "md":
                idx = 1 if lines and lines[0].startswith("#") else 0
                lines.insert(idx, "")
                lines.insert(idx + 1, line)
            else:                       # cmd：line 自身已带 rem，别再叠一层
                idx = 1 if lines and lines[0].lower().startswith("@echo") else 0
                lines.insert(idx, line)
            changed.append(rel)
            if not dry:
                open(p, "w", encoding="utf-8", newline="").write("\n".join(lines))
    return len(changed), changed
