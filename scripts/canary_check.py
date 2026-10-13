# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:64acc6cdfc · 溯源标记，请保留
"""canary_check.py —— 金丝雀溯源自检 / 取证工具。

═══ 两个用途 ═══
1. **自查**（发布前跑）：确认所有该埋点的文件都埋了，指纹与路径一致。
2. **取证**（怀疑被抄时跑）：把**别人的**代码目录丢进来，看有没有我们的标记 ——
   命中即证明"这份代码源自 manju-vidu-workflow"。

用法：
    python scripts/canary_check.py .                    # 自查当前仓库
    python scripts/canary_check.py <别人的目录>          # 取证
    python scripts/canary_check.py . --json out.json    # 机器可读输出
    python scripts/canary_check.py . --mark             # 补齐缺失的埋点（仅自己仓库用）

退出码：0 = 正常；1 = 自查发现缺失；2 = 取证命中（★ 命中时可用于工单/存证）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _canary import PRODUCT, SIG, VERSION, scan, apply_marks, fingerprint  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="金丝雀溯源自检 / 取证")
    ap.add_argument("root", help="要扫描的目录")
    ap.add_argument("--json", dest="json_out", default="", help="结果另存 JSON")
    ap.add_argument("--mark", action="store_true", help="补齐缺失埋点（幂等）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    if not os.path.isdir(root):
        print("目录不存在: %s" % root)
        return 1

    if a.mark:
        n, files = apply_marks(root)
        print("已补齐 %d 个文件的埋点" % n)
        for f in files:
            print("   + %s" % f)
        print()

    r = scan(root)
    marked, unmarked, needles, foreign = (r["marked"], r["unmarked"], r["needles"], r["foreign"])
    print("=" * 64)
    print(" 金丝雀检查 · %s v%s（标记前缀 %s）" % (PRODUCT, VERSION, SIG))
    print(" 扫描目录: %s" % root)
    print("=" * 64)
    print("  带标记文件   : %d" % len(marked))
    print("  文档暗针     : %d  %s" % (len(needles), ("→ " + ", ".join(needles[:3])) if needles else ""))
    print("  未埋点（应埋没埋）: %d" % len(unmarked))
    for f in unmarked[:12]:
        print("      ! %s" % f)
    if len(unmarked) > 12:
        print("      … 另有 %d 个" % (len(unmarked) - 12))
    print("  ★ 指纹错位（疑似搬运）: %d" % len(foreign))
    for f, fp in foreign[:12]:
        print("      !! %s   该文件内指纹=%s，按其路径应为=%s" % (f, fp, fingerprint(f)))

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump({"root": root, "product": PRODUCT, "version": VERSION,
                       "marked": marked, "unmarked": unmarked,
                       "needles": needles, "foreign": foreign},
                      f, ensure_ascii=False, indent=1)
        print("\n  结果已存: %s" % a.json_out)

    # ---- 判定 ----
    # 取证场景：目录里出现我们的标记，但根目录不是本仓库（无 .canary_root）⇒ 命中
    is_self = os.path.exists(os.path.join(root, ".canary_root"))
    print("-" * 64)
    if not is_self and (marked or needles):
        print("★ 取证命中：该目录含 %d 处本产品标记、%d 处文档暗针。"
              % (len(marked), len(needles)))
        print("   ⇒ 可判定其内容**源自 %s**（标记为源码级溯源，非偶然雷同）。" % PRODUCT)
        for f, fp in marked[:10]:
            print("      %s   %s:%s" % (f, SIG, fp))
        return 2
    if is_self and (unmarked or foreign):
        print("✗ 自查未通过：有 %d 个文件未埋点、%d 个指纹错位。"
              % (len(unmarked), len(foreign)))
        print("   修法：python scripts/canary_check.py . --mark")
        return 1
    print("✓ 检查通过。" + ("（自查：埋点齐、指纹一致）" if is_self else "（未发现本产品标记）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
