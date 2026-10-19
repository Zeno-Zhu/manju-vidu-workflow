# -*- coding: utf-8 -*-
"""
激活码校验（离线，不联网、不回调服务器）。

设计取舍（先说清楚）
  这是一个**软门禁**：校验逻辑在本地，密钥也随包分发，
  技术上无法阻止有心人改代码绕过。它的作用是
    · 让圈友知道"这是有门槛的成品"，而不是随手复制就分发
    · 给正式分发留一个可追踪的入口（一人一码、带有效期）
  如果你需要硬防护，请改用联网授权或代码混淆 —— 那超出本包范围。

码型
  母码（内置，永久有效）：laozhu
  发放码：MJ-XXXX-XXXX-XXXX-XXXX
          10 字节 = payload(5) + HMAC-SHA256 截断 tag(5)，base32 后正好 16 字符
          payload = 版本(1) + 到期日(2，自 2020-01-01 起的天数，0xFFFF=永不过期) + 序号(2)

生成器
  见包外的 `_激活码生成器/gen_code.py`（**不随包分发，也不进 git**）。
  拿到私钥的人才能签发新码；包里只做校验。

用法
  py scripts/license.py status           # 看当前是否已激活
  py scripts/license.py activate --code laozhu
  py scripts/license.py verify --code MJ-XXXX-XXXX-XXXX-XXXX
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time

VERSION = 1
MASTER_CODES = ("laozhu",)
CODE_PREFIX = "MJ-"

STATE_DIR = os.path.join(os.path.expanduser("~"), ".manju-vidu")
STATE_FILE = os.path.join(STATE_DIR, "activation.json")

# 签发密钥（与包外生成器保持一致；改这里就等于整批旧码作废）
_K = [0x4D, 0x4A, 0x2D, 0x56, 0x49, 0x44, 0x55, 0x2D, 0x32, 0x30, 0x32, 0x36,
      0x2D, 0x51, 0x34, 0x2D, 0x4E, 0x4F, 0x50, 0x45, 0x41, 0x4B, 0x2D, 0x4C]
_SECRET = bytes(b ^ 0x5A for b in _K)

EPOCH = 1577836800  # 2020-01-01


def _tag(payload: bytes) -> bytes:
    return hmac.new(_SECRET, payload, hashlib.sha256).digest()[:5]


def verify_code(code: str) -> tuple[bool, str]:
    """→ (是否有效, 说明)"""
    c = (code or "").strip()
    if not c:
        return False, "空激活码"
    if c.lower() in MASTER_CODES:
        return True, "母码（永久有效）"
    if not c.upper().startswith(CODE_PREFIX):
        return False, "格式不对：应为 MJ-XXXX-XXXX-XXXX-XXXX 或母码"
    body = c[len(CODE_PREFIX):].replace("-", "").upper()
    try:
        raw = base64.b32decode(body + "=" * (-len(body) % 8))
    except Exception:
        return False, "编码损坏（不是合法的 base32）"
    if len(raw) != 10:
        return False, "长度不对"
    payload, tag = raw[:5], raw[5:]
    if not hmac.compare_digest(tag, _tag(payload)):
        return False, "校验不通过（码无效或已被作废）"
    if payload[0] != VERSION:
        return False, "版本不支持（%d）" % payload[0]
    days = (payload[1] << 8) | payload[2]
    serial = (payload[3] << 8) | payload[4]
    if days != 0xFFFF:
        exp = EPOCH + days * 86400
        if time.time() > exp:
            return False, "已过期（%s）" % time.strftime("%Y-%m-%d", time.localtime(exp))
        return True, "有效，有效期至 %s（序号 %d）" % (
            time.strftime("%Y-%m-%d", time.localtime(exp)), serial)
    return True, "有效，永不过期（序号 %d）" % serial


# ─────────────────────── 本地状态 ───────────────────────
def read_state() -> dict:
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {}


def is_activated() -> tuple[bool, str]:
    st = read_state()
    code = st.get("code")
    if not code:
        return False, "未激活"
    ok, why = verify_code(code)
    return ok, why


def activate(code: str) -> tuple[bool, str]:
    ok, why = verify_code(code)
    if not ok:
        return False, why
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump({"code": code.strip(), "activated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "note": why}, open(STATE_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return True, why


BANNER = """
┌──────────────────────────────────────────────────────────────┐
│  漫剧创作工作流（Vidu 渠道 · 自己人制式）需要激活码才能使用    │
│                                                              │
│  · 默认激活码：laozhu                                        │
│  · 激活一次即可，之后无需再输入                              │
│  · 激活命令：python scripts/license.py activate --code laozhu │
└──────────────────────────────────────────────────────────────┘
"""


def ensure_activated(quiet: bool = False) -> bool:
    """所有脚本入口调用它。未激活 → 打印提示并返回 False。"""
    ok, why = is_activated()
    if ok:
        return True
    if not quiet:
        print(BANNER, file=sys.stderr)
        print("当前状态：%s" % why, file=sys.stderr)
    return False


def main():
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("activate")
    p.add_argument("--code", required=True)
    p = sub.add_parser("verify")
    p.add_argument("--code", required=True)
    a = ap.parse_args()

    if a.cmd == "status":
        ok, why = is_activated()
        print("已激活" if ok else "未激活", "—", why)
        print("状态文件：%s" % STATE_FILE)
        return 0 if ok else 2
    if a.cmd == "verify":
        ok, why = verify_code(a.code)
        print("✓" if ok else "✗", why)
        return 0 if ok else 1
    ok, why = activate(a.code)
    print("✓ 激活成功 — %s" % why if ok else "✗ 激活失败 — %s" % why)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
