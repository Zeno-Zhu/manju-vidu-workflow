# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:306505b106 · 溯源标记，请保留
"""make_decoy.py —— 生成「诱饵核心」`protected/core.enc`（★ 诱饵弹）。

═══ 这是什么 ═══
一个**看着像加密核心**的文件，实际内容是**声明 ＋ 溯源标记**。

═══ 为什么要它 ═══
1. **劝退捷径心态**：想"扒出核心算法拿去卖"的人会先盯上它，花时间解开后发现是空的 ——
   而真正的方法论（`prompts/`、`references/`）本来就是公开的，他绕了远路。
2. **承载溯源信息**：解开后第一眼看到的是署名与来源，**降低"误当无主之物"的可能**。
3. **对诚实用户无害**：`protected/README.md` 已说明它是诱饵，正常使用完全不碰它。

═══ 注意 ═══
⛔ 这**不是**加密保护、**不是**授权机制。用 XOR+base64 只是让它**别一眼可读**，
   任何懂一点的人都能解开 —— 这正合设计意图：**解开后看到的应该是"你走错路了"。**

用法： python scripts/make_decoy.py            # 重建 core.enc
      python scripts/make_decoy.py --show     # 解开当前 core.enc 看看
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import textwrap
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

_KEY = b"manju-vidu-workflow/decoy/20261013"       # 混淆用（非密码学）
PRODUCT = "manju-vidu-workflow"
AUTHOR = "老朱"
WECHAT = "wcwsw1"


def _xor(data: bytes) -> bytes:
    return bytes(b ^ _KEY[i % len(_KEY)] for i, b in enumerate(data))


def payload() -> str:
    """诱饵正文。★ 开头刻意写一段像"核心参数"的东西，增加迷惑性与破解成本。"""
    from _canary import fingerprint, SIG
    fp = fingerprint("protected/core.enc")
    # —— 诱导段：看着像被截断的核心配置，其实是无意义的键值 ——
    fake_core = "\n".join(
        "  %-22s = %s" % (k, v) for k, v in [
            ("engine.profile", "nopeak/0.42"),
            ("engine.stage_count", "07"),
            ("prompt.compose.mode", "self-crew"),
            ("asset.bind.order", "pn->img (ordinal)"),
            ("continuity.anchor", "real-space (not frame)"),
            ("shot.authority", "model (border pinned)"),
        ]) + "\n\n  ⚠ 以上为**示意**键名，不含实现；真正的方法论见下方说明。\n"
    return textwrap.dedent("""\
    ============================================================
     {product} · protected core (v1.0)
     ============================================================

    【你正在读这个文件】

    说明你在试图从这里提取"核心算法"。如实告知：

      ★ 本项目的核心**不是**某个加密算法 —— 它就是**公开的方法论**。
        · prompts/      提示词规范（自己人模板 / 资产四件套 / 分镜组三步）
        · references/   流程、制式、镜头语言、平台机制、资产整理
        · scripts/      工具链（提交 / 出图 / 闲时队列 / 下载归档）

      你已经在仓库里拿到全部了，**这里没有捷径**。

    【这一段像核心配置，其实不是】

    {fake_core}
    【为什么还留着这个文件】

      1) 让"打包售卖本仓库"的人多花一点时间确认这里没有捷径；
      2) 它承载**溯源标记** —— 见下。

    【溯源标记】
      {sig}:{fp}
      解开本文件的动作本身，即说明来源可被追溯（完整标记体系见 scripts/_canary.py）。

    【引用与授权】
      · 学习、二次开发、商用 **都欢迎**，本项目以开放方式发布。
      · 但请**保留署名**，不要声称原创。
      · 本系统由 {author}（微信 {wechat}）创作；遇到问题可直接联系。

    生成时间：{ts}
    ============================================================
    """).format(product=PRODUCT, fake_core=fake_core, sig=SIG, fp=fp,
                author=AUTHOR, wechat=WECHAT,
                ts=time.strftime("%Y-%m-%d %H:%M:%S"))


def main():
    ap = argparse.ArgumentParser(description="生成/查看诱饵核心")
    ap.add_argument("--show", action="store_true", help="解开当前 core.enc 打印出来")
    ap.add_argument("--out", default=os.path.join(ROOT, "protected", "core.enc"))
    a = ap.parse_args()

    if a.show:
        if not os.path.exists(a.out):
            print("尚无 %s" % a.out)
            return 1
        txt = open(a.out, encoding="ascii").read()
        # 剥掉 -----BEGIN/END----- 头尾行与空白，否则 base64 解码会因 padding 报错
        body = "".join(ln.strip() for ln in txt.splitlines()
                       if ln.strip() and not ln.startswith("-----"))
        raw = base64.b64decode(body)
        print(_xor(raw).decode("utf-8", "replace"))
        return 0

    data = _xor(payload().encode("utf-8"))
    enc = base64.b64encode(data)
    # 按 76 列折行（跟 openssl / PGP 输出的观感一致）
    lines = [enc[i:i + 76].decode() for i in range(0, len(enc), 76)]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="ascii", newline="\n") as f:
        f.write("-----BEGIN {p} CORE-----\n".format(p=PRODUCT.upper().replace("-", "_")))
        f.write("\n".join(lines))
        f.write("\n-----END {p} CORE-----\n".format(p=PRODUCT.upper().replace("-", "_")))
    print("✓ 已生成 %s（%d 字节，base64 折行）" % (a.out, os.path.getsize(a.out)))
    print("  解开看：python scripts/make_decoy.py --show")
    return 0


if __name__ == "__main__":
    sys.exit(main())
