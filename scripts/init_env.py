# -*- coding: utf-8 -*-
"""init_env.py —— 一次性环境自检 / 安装（★ 用户双击 `1-初始化环境.cmd` 时被调用）。

═══ 它解决什么 ═══
用户机器**可能没有 Python / 没装依赖 / 没装 Edge**。本脚本**只做检查与安装**，
不做任何业务；跑完把「下一步该干什么」明确打出来。

═══ 检查项 ═══
  1. Python 版本（≥3.8；本包只用标准库 ＋ 一个 websocket-client）
  2. 依赖 `websocket-client`（缺就 pip 装；装不上给出手动命令）
  3. Microsoft Edge（找不到就提示下载）
  4. 浏览器实例与登录态（`vidu_edge.py status`）
  5. 桌面快捷方式（`vidu_shortcuts.py`）

用法： python init_env.py [--no-pip] [--no-lnk]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

OK, WARN, BAD = "[OK]", "[! ]", "[X ]"


def run(cmd, timeout=300):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return -1, str(e)


def step_python():
    v = sys.version_info
    print("%s Python %d.%d.%d  (%s)" % (OK if v >= (3, 8) else BAD, v.major, v.minor,
                                        v.micro, sys.executable))
    if v < (3, 8):
        print("     本包需要 Python 3.8+，请升级后重试。")
        return False
    return True


def step_dep(no_pip=False):
    try:
        import websocket  # noqa: F401
        print("%s 依赖 websocket-client 已安装" % OK)
        return True
    except ImportError:
        pass
    if no_pip:
        print("%s 缺依赖 websocket-client（--no-pip，跳过安装）" % WARN)
        return False
    print("%s 缺依赖 websocket-client —— 正在安装…" % WARN)
    rc, out = run([sys.executable, "-m", "pip", "install", "websocket-client"])
    if rc == 0:
        print("%s websocket-client 安装完成" % OK)
        return True
    rc2, out2 = run([sys.executable, "-m", "pip", "install", "--user", "websocket-client"])
    if rc2 == 0:
        print("%s websocket-client 安装完成（--user）" % OK)
        return True
    print("%s 安装失败。请手动执行：" % BAD)
    print("     \"%s\" -m pip install websocket-client" % sys.executable)
    print("     （若在国内网络，可加： -i https://pypi.tuna.tsinghua.edu.cn/simple ）")
    print("     最后几行输出：%s" % out2.strip().splitlines()[-1:] or out.strip()[-200:])
    return False


def step_edge():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            print("%s Microsoft Edge 已安装" % OK)
            print("     %s" % p)
            return True
    print("%s 没找到 Microsoft Edge。" % WARN)
    print("     Windows 10/11 一般自带；没有请到这里装：https://www.microsoft.com/edge")
    return False


def step_instance(account_index):
    edge_py = os.path.join(HERE, "vidu_edge.py")
    rc, out = run([sys.executable, edge_py, "status", "--account-index", str(account_index)],
                  timeout=90)
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-4:]
    if rc == 0 and any("已登录" in ln or "就绪" in ln for ln in tail):
        print("%s 浏览器实例可用且**已登录**" % OK)
        for ln in tail:
            print("     %s" % ln)
        return "logged"
    print("%s 浏览器实例未就绪 / 未登录" % WARN)
    for ln in tail:
        print("     %s" % ln)
    print("     ⇒ 双击桌面的「Vidu 账号%d - 启动」，在弹出的 Edge 窗口里**登录 Vidu 账号**；" % account_index)
    print("        登录态会存在该账号独立的 profile 里，之后不用再登。")
    return "need_login"


def step_shortcuts(account_index, no_lnk=False):
    if no_lnk:
        print("%s 桌面快捷方式（--no-lnk 跳过）" % WARN)
        return
    sh = os.path.join(HERE, "vidu_shortcuts.py")
    rc, out = run([sys.executable, sh, "--account-index", str(account_index)], timeout=180)
    if rc == 0:
        print("%s 桌面快捷方式已生成（Vidu 账号%d - 启动 / 关闭）" % (OK, account_index))
    else:
        print("%s 快捷方式生成失败（不影响命令行使用）" % WARN)
        print("     可手动跑：\"%s\" \"%s\"" % (sys.executable, sh))


def main():
    ap = argparse.ArgumentParser(description="Vidu 渠道 skill —— 一次性环境自检")
    ap.add_argument("--account-index", type=int, default=1)
    ap.add_argument("--no-pip", action="store_true")
    ap.add_argument("--no-lnk", action="store_true")
    a = ap.parse_args()

    print("=" * 62)
    print(" 漫剧创作工作流 · Vidu 渠道 —— 环境自检")
    print(" 包目录: %s" % SKILL_ROOT)
    print("=" * 62)
    print()
    ok_py = step_python()
    ok_dep = step_dep(a.no_pip) if ok_py else False
    ok_edge = step_edge()
    state = step_instance(a.account_index) if (ok_py and ok_dep) else "skip"
    step_shortcuts(a.account_index, a.no_lnk)

    print()
    print("-" * 62)
    if ok_py and ok_dep and ok_edge:
        if state == "logged":
            print("%s 环境全部就绪 —— 可以直接开始用了。" % OK)
            print("     把 `SKILL.md` 交给 AI（或把本目录作为工作目录），跟它说「初始化」即可。")
        else:
            print("%s 环境已就绪，但**还没登录 Vidu**。" % WARN)
            print("     ⇒ 双击桌面的「Vidu 账号%d - 启动」完成一次登录，再跑一遍本脚本确认。" % a.account_index)
    else:
        print("%s 还有项目没配好，请按上面的提示处理后再跑一次。" % BAD)
        print("     （装完 Python 后**本窗口看不到新 PATH 是正常的**：关掉重开再跑即可。）")
    print("-" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
