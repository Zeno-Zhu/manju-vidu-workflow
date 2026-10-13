# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:2f7421e1b2 · 溯源标记，请保留
"""
vidu_edge.py —— Vidu 渠道「浏览器实例」的唯一入口：开 / 查 / 关 / 重启。

═══ 为什么必须有这个文件（四条都是踩出来的，别绕过它直接敲命令）═══

1. ★ 会话启动的 GUI 进程会被宿主回收
   —— 从 Bash / PowerShell 工具里用 `&`、`Start-Process`、`Popen` 启的 Edge，
   会在那条命令结束时被连带清掉（表现为「Edge 开了几秒就没了」）。
   ⇒ 必须用 **WMI `Win32_Process.Create`** 启动，才能脱离会话。

2. ★ Edge 136+ 对**默认 user-data-dir** 直接忽略 `--remote-debugging-port`
   —— 本机 Edge 154 命中。进程命令行明明带着参数，端口就是不监听。
   ⇒ 必须用 **junction** 把真实 `Edge\\User Data` 映射成一个
     **路径字符串不同**的新路径，拿它当 `--user-data-dir`。
     好处：零复制、登录态 / 扩展 100% 保留。

3. ★ 同一 profile 已有 Edge 在跑时，新实例只会「转发命令行然后退出」
   —— 不会开调试端口，也不会报错。
   ⇒ `up` 先体检；有冲突实例时必须**先优雅关掉**（`--close-existing`）。

4. ★ 关浏览器一律**优雅关闭**，绝不 `taskkill /F`
   —— 强杀会让未落盘的 cookie 丢失（登录态就这么没的）。
   ⇒ `down` 走 CDP `Browser.close`；关别家 Edge 走 `WM_CLOSE`（等价于点 ×）。

═══ 用法 ═══
  python vidu_edge.py up      [--port 9222] [--profile Default] [--url ...] [--close-existing]
  python vidu_edge.py status  [--port 9222]
  python vidu_edge.py down    [--port 9222]
  python vidu_edge.py restart [--close-existing]

★ 必须在**非沙箱**下运行：沙箱内连 `127.0.0.1` 都会「积极拒绝」（假失败）。
★ 另：本机 `curl` 走代理，所以这里一律 `urllib` + `ProxyHandler({})` 直连。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EDGE_EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
REAL_UDD = r"C:\Users\Administrator\AppData\Local\Microsoft\Edge\User Data"
# ★ 默认：**独立**的自动化 profile（物理独立 ⇒ 单例锁独立 ⇒ 永不被用户的 Edge 挟持）
DEDICATED_UDD = r"C:\Users\Administrator\.manju-vidu\vidu-profile"
# 可选：用 junction 指向真实 Edge Default profile（想蹭用户的登录态/扩展时才用）
LINK_UDD = r"C:\Users\Administrator\.workbuddy\edge-cdp"
STATE_DIR = r"C:\Users\Administrator\.manju-vidu"
STATE_FILE = os.path.join(STATE_DIR, "edge-instance.json")
DEFAULT_URL = "https://www.vidu.cn/create/character2video"
LOG = os.path.join(STATE_DIR, "vidu_edge.log")

# ══ ★★ 多账号：一账号 = 一实例 = 一 profile ══
#   账号 N → profile `~/.manju-vidu/profiles/账号N`、状态 `~/.manju-vidu/instances/账号N.json`、
#   默认端口 `9221+N`（账号1→9222，账号2→9223…）。
#   这样多账号互不干扰（单例锁、登录态、cookie 全独立），可同时跑。
ACCOUNT_ROOT = os.path.join(STATE_DIR, "profiles")
INSTANCE_DIR = os.path.join(STATE_DIR, "instances")


def acct_key(name):
    """账号名 → 安全的目录/文件名（保留中文与数字，去掉非法字符）。"""
    import re as _re
    k = _re.sub(r'[\\/:*?"<>|\s]+', "_", (name or "账号1").strip()) or "账号1"
    return k


def acct_index(name):
    """从账号名里提序号：`账号3` → 3。

    ★ 名字里没有数字时（如「张三」），**顺延到已用序号的下一号** ——
      否则会退回 1、跟「账号1」抢同一个端口（实测会撞车）。
    """
    import re as _re
    m = _re.search(r"(\d+)", name or "")
    if m:
        return int(m.group(1))
    used = []
    if os.path.isdir(INSTANCE_DIR):
        for fn in os.listdir(INSTANCE_DIR):
            mm = _re.search(r"(\d+)", fn)
            if mm:
                used.append(int(mm.group(1)))
    return (max(used) + 1) if used else 1


def acct_paths(name):
    """(profile 目录, 状态文件, 默认端口)。"""
    k = acct_key(name)
    return (os.path.join(ACCOUNT_ROOT, k),
            os.path.join(INSTANCE_DIR, k + ".json"),
            9221 + acct_index(name))


def list_accounts():
    """列出已有实例（从 instances/*.json 读）。"""
    out = []
    if not os.path.isdir(INSTANCE_DIR):
        return out
    for fn in sorted(os.listdir(INSTANCE_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(INSTANCE_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
            out.append({"account": fn[:-5], "port": d.get("port"),
                        "pid": d.get("pid"), "started_at": d.get("started_at")})
        except Exception:
            out.append({"account": fn[:-5]})
    return out


def note(msg, echo=True):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if echo:
        print(msg)


# ---------------------------------------------------------------- WMI / 进程
def wmi_service():
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    loc = win32com.client.Dispatch("WbemScripting.SWbemLocator")
    return loc.ConnectServer(".", "root\\cimv2")


def msedge_main_processes(svc=None):
    """返回「浏览器主进程」（排除 --type=... 的子进程）：[{pid, cmdline}]"""
    svc = svc or wmi_service()
    out = []
    for p in svc.ExecQuery("SELECT ProcessId,CommandLine FROM Win32_Process WHERE Name='msedge.exe'"):
        cl = p.CommandLine or ""
        if "--type=" in cl:
            continue
        out.append({"pid": int(p.ProcessId), "cmdline": cl})
    return out


def launch_detached(cmdline):
    """★ 用 WMI 创建，脱离当前会话（这是唯一实测能活下来的方式）。"""
    svc = wmi_service()
    cls = svc.Get("Win32_Process")
    inp = cls.Methods_("Create").InParameters.SpawnInstance_()
    inp.Properties_.Item("CommandLine").Value = cmdline
    out = svc.ExecMethod("Win32_Process", "Create", inp)
    rc = out.Properties_.Item("ReturnValue").Value
    pid = out.Properties_.Item("ProcessId").Value
    if rc != 0:
        # 退路：脱离 Job 启动（Windows 上把宿主 Job 的 kill-on-close 甩掉）
        CREATE_BREAKAWAY_FROM_JOB = 0x01000000
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        try:
            pr = subprocess.Popen(cmdline, creationflags=(CREATE_BREAKAWAY_FROM_JOB
                                                          | DETACHED_PROCESS
                                                          | CREATE_NEW_PROCESS_GROUP),
                                  close_fds=True)
            pid, rc = pr.pid, 0
        except Exception as e:
            return None, "WMI rc=%s 且 Popen 兜底失败: %s" % (rc, e)
    return pid, None


def _threads_of(pids):
    TH32CS_SNAPTHREAD = 0x00000004

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD)]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap in (0, -1):
        return []
    te = THREADENTRY32()
    te.dwSize = ctypes.sizeof(THREADENTRY32)
    out = []
    ok = k32.Thread32First(snap, ctypes.byref(te))
    while ok:
        if te.th32OwnerProcessID in pids:
            out.append(te.th32ThreadID)
        ok = k32.Thread32Next(snap, ctypes.byref(te))
    k32.CloseHandle(snap)
    return out


def send_wm_close(pids, timeout=45):
    """给这些 PID 的顶层窗口发 WM_CLOSE（等价于点 ×，**不是强杀**）。

    返回 (可见窗口数, 全部窗口数, 是否已退出)。
    ★ 必须分开数：Edge 的预启动进程虽然"没有窗口"，却挂着 crashpad / IME 这类
      **隐藏**句柄 —— 只看总数会把"纯预启动进程"误判成"用户在用的会话"，
      于是该补 WM_QUIT 的时候不敢补（实测踩过），进程就永远退不掉。
    """
    pids = set(pids)
    u32 = ctypes.windll.user32
    u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    vis, allw = [], []

    def cb(hwnd, lparam):
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            allw.append(hwnd)
            if u32.IsWindowVisible(hwnd):
                vis.append(hwnd)
        return True

    u32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
    for h in allw:
        u32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not ({p["pid"] for p in msedge_main_processes()} & pids):
            return len(vis), len(allw), True
        time.sleep(1)
    return len(vis), len(allw), False


def quit_threads(pids, timeout=30):
    """★ 只对「Edge 预启动进程」用：它没有**可见**窗口，WM_CLOSE 够不着。
    给它的线程发 WM_QUIT，请它自己退出消息循环 —— 仍是**优雅请求**，不是 TerminateProcess。"""
    pids = set(pids)
    u32 = ctypes.windll.user32
    tids = _threads_of(pids)
    for t in tids:
        u32.PostThreadMessageW(t, 0x0012, 0, 0)  # WM_QUIT
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not ({p["pid"] for p in msedge_main_processes()} & pids):
            return len(tids), True
        time.sleep(1)
    return len(tids), False


def end_gracefully(pids, quit_threads_too=False, timeout=45):
    """优雅结束这些进程。

    ★ 安全次序（很重要）：
      ① 先 WM_CLOSE（含隐藏句柄）—— 等效点 ×，Edge 会正常收尾并**刷盘 cookie**；
      ② 只有在「**没有任何可见窗口**」时才补 WM_QUIT —— 那说明对方是纯粹的
         "启动加速"预启动进程，没有用户在用的会话。
      ③ 若有可见窗口却迟迟不退，**宁可报失败也不补 WM_QUIT** ——
         对真实会话补 WM_QUIT 有可能打断 cookie 落盘，那正是掉登录的成因之一。

    返回 (处理数, 是否已退出)。
    """
    n_vis, n_all, done = send_wm_close(pids, timeout=timeout)
    n2 = 0
    if not done and quit_threads_too:
        if n_vis == 0:
            note("   无可见窗口（%d 个隐藏句柄）⇒ 判定为预启动进程，补 WM_QUIT" % n_all)
            n2, done = quit_threads(pids, timeout=timeout)
        else:
            note("   ⚠ 有 %d 个可见窗口但未退出，**不补 WM_QUIT**（保护会话/cookie 落盘），"
                 "请手动关闭后重试。" % n_vis)
    return n_all + n2, done


# ---------------------------------------------------------------- junction
def ensure_junction():
    if os.path.exists(LINK_UDD):
        real = os.path.realpath(LINK_UDD).rstrip("\\")
        if real.lower() == REAL_UDD.rstrip("\\").lower():
            return True, "junction 已就绪"
        return False, "★ %s 已存在但指向 %s（不是 %s）—— 请人工确认" % (LINK_UDD, real, REAL_UDD)
    try:
        import _winapi
        _winapi.CreateJunction(REAL_UDD, LINK_UDD)
        return True, "已创建 junction: %s -> %s" % (LINK_UDD, REAL_UDD)
    except Exception as e:
        return False, "★ 建 junction 失败: %s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------- CDP 探测
def _http_json(port, path, timeout=3):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open("http://127.0.0.1:%d%s" % (port, path), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def cdp_alive(port):
    try:
        return _http_json(port, "/json/version")
    except Exception:
        return None


def login_state(port):
    """走 CDP 的 Network.getCookies（活内存，最权威）。"""
    try:
        from vidu_cdp import CDP
        with CDP(port=port, url_contains="vidu.cn", timeout=15) as c:
            ck = c.send("Network.getCookies", {"urls": [
                "https://www.vidu.cn", "https://service.vidu.cn", "https://vidu.cn"]})
            names = {x["name"] for x in ck.get("cookies", [])}
            logged = "JWT" in names
            href = c.eval("location.href")
            return logged, sorted(names), href
    except Exception as e:
        return None, [], "查询失败: %s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------- 子命令
def account_of(a):
    """按 `--account` 推导 (profile 目录, 状态文件, 默认端口)；`--udd` 显式给了就优先。"""
    udd, state, port = acct_paths(getattr(a, "account", "账号1"))
    return (getattr(a, "udd", "") or udd), state, port


def ensure_udd(a):
    """决定本次用哪个 user-data-dir，并把它准备好。

    ★★ 2026-10-13 多账号改造：**一账号一个 profile**（`~/.manju-vidu/profiles/<账号>`）。
       · 账号之间**单例锁 / 登录态 / cookie 全独立** ⇒ 可以同时跑多个实例；
       · 端口默认 `9221+账号序号`（账号1→9222、账号2→9223…）；
       · 状态各自落 `~/.manju-vidu/instances/<账号>.json`。

    ★ 两种模式（本文件最重要的一次架构取舍）：
      默认 = **独立目录**（按账号）
        - 物理目录独立 ⇒ 单例锁独立 ⇒ **用户的 Edge / 「启动加速」预启动进程完全不相干**；
        - 路径不是默认路径 ⇒ Edge 136+ 的加固也不适用；
        - 代价：登录态/扩展**不与用户日常 Edge 共享**。
      `--junction` = 用真实的 Edge Default profile（junction 映射）
        - 好处：蹭用户现成的登录态/扩展；
        - ★ 代价（实测踩死）：junction 让两个路径**指向同一物理目录**，于是和默认目录
          共用同一把单例锁 —— Edge 的「启动加速」预启动进程（`--no-startup-window`，
          无可见窗口、WM_QUIT 也不吃）会一直占着锁，把我们的命令行吞掉、端口永远起不来。
    """
    if a.junction:
        ok, msg = ensure_junction()
        note(msg)
        return (LINK_UDD, ok)
    udd, _, _ = account_of(a)
    os.makedirs(udd, exist_ok=True)
    return (udd, True)


def active_udd(a):
    if a.junction:
        return LINK_UDD
    return account_of(a)[0]


def build_cmdline(a):
    return ('"%s" --remote-debugging-port=%d --user-data-dir=%s'
            ' --profile-directory=%s --no-first-run --no-default-browser-check'
            ' --remote-allow-origins=* %s'
            % (EDGE_EXE, a.port, active_udd(a), a.profile, a.url))


def classify(mains, udd):
    """把 msedge 主进程分三类（都**只针对当前这条渠道的 udd**）：
    ours      = 带本渠道 --user-data-dir 的实例
    prelaunch = Edge「启动加速」预启动进程（--no-startup-window，无窗口）
    other     = 别的 Edge 主进程
    ★ 独立目录模式下，prelaunch / other 用的是**另一个物理目录**，
      和我们的单例锁无关 ⇒ **不是冲突**，不该动它们。
    """
    ours, pre, other = [], [], []
    for p in mains:
        cl = p["cmdline"].lower()
        if udd.lower() in cl:
            ours.append(p)
        elif "--no-startup-window" in cl or "--prefetch" in cl:
            pre.append(p)
        else:
            other.append(p)
    return ours, pre, other


def cmd_up(a):
    v = cdp_alive(a.port)
    if v:
        note("实例已在运行：%s（端口 %d）" % (v.get("Browser"), a.port))
        _report_login(a.port)
        return 0

    udd, ok = ensure_udd(a)
    if not ok:
        return 1
    mode = "junction(真实 Default profile)" if a.junction else "独立自动化 profile"
    note("模式：%s\n     user-data-dir = %s" % (mode, udd))

    ours, pre, other = classify(msedge_main_processes(), udd)
    if ours:
        note("★ 本渠道已有主进程 %s 但端口 %d 不通 —— 先优雅关掉再重开" % ([p["pid"] for p in ours], a.port))
        n, done = end_gracefully([p["pid"] for p in ours], quit_threads_too=True)
        note("   处理 %d 个进程，%s" % (n, "已完成" if done else "★超时"))
        if not done:
            note("   ⇒ 该实例没退干净，先停手（不硬杀）。请手动关掉那个 Edge 窗口后重试。")
            return 2
        time.sleep(2)

    if not a.junction:
        # 独立目录模式：别人的 Edge / 预启动进程都用**另一个物理目录**，跟我们无关
        if pre or other:
            note("（信息）另有 %d 个其它 Edge 主进程在跑，用的是别的 profile，**与本渠道无关**，不动它们。"
                 % (len(pre) + len(other)))
    else:
        if other:
            note("★ 检测到 %d 个「用户的 Edge」主进程占着**同一个** profile：" % len(other))
            for p in other:
                note("   pid=%s  %s" % (p["pid"], p["cmdline"][:110]))
            if not a.close_existing:
                note("   ⇒ 加 `--close-existing` 我用 WM_CLOSE 优雅关掉（等价点 ×，不是强杀）；")
                note("     或者改用默认的独立目录模式（重跑本命令、去掉 --junction）—— 那样永不受它影响。")
                return 2
            n, done = end_gracefully([p["pid"] for p in other])
            if not done:
                note("   ★ 没关掉。改用默认的独立目录模式更省事：去掉 --junction 重跑。")
                return 2
            time.sleep(2)
        if pre:
            note("★ 提示：Edge「启动加速」预启动进程 pid=%s 正占着同一个 profile ——"
                 " 实测它会把命令行吞掉、端口永远起不来，且 WM_QUIT 也赶不走。"
                 % [p["pid"] for p in pre])
            note("   ⇒ 这就是为什么要用**默认的独立目录模式**：去掉 --junction 重跑本命令即可。")

    cmd = build_cmdline(a)
    note("启动（WMI，脱离会话）：%s" % cmd)
    pid, err = launch_detached(cmd)
    if err:
        note("★ 启动失败：%s" % err)
        return 1
    note("主进程 pid=%s，等待调试端口 %d …" % (pid, a.port))

    t0 = time.time()
    while time.time() - t0 < a.wait:
        if cdp_alive(a.port):
            break
        time.sleep(1)
    v = cdp_alive(a.port)
    if not v:
        note("★ 超时 %ss，端口 %d 仍不通。" % (a.wait, a.port))
        ours2, pre2, other2 = classify(msedge_main_processes(), udd)
        if ours2:
            note("   我们的主进程 %s 还活着但没开端口 ⇒ 不是被吞命令行。" % [p["pid"] for p in ours2])
            note("   可能原因：user-data-dir 落在了默认路径（加固会吞调试端口），或 --port 冲突。")
        else:
            note("   我们的启动进程已经退出 ⇒ 命令行被**转发**给了别的实例（单例锁冲突）。")
            if pre2:
                note("   嫌疑：Edge 预启动进程 pid=%s 占着同一个 profile。" % [p["pid"] for p in pre2])
        note("   ⇒ 首选解法：用默认的独立目录模式（去掉 --junction 重跑）。")
        return 1
    note("就绪：%s" % v.get("Browser"))

    inst = {"account": getattr(a, "account", ""), "pid": pid, "port": a.port,
            "profile": a.profile, "url": a.url,
            "user_data_dir": udd, "mode": mode,
            "started_at": datetime.now().isoformat(), "browser": v.get("Browser")}
    _, state_file, _ = account_of(a)
    try:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        json.dump(inst, open(state_file, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        note("实例信息已留痕 -> %s" % state_file)
    except OSError:
        pass
    _report_login(a.port)
    return 0


def _report_login(port):
    logged, names, href = login_state(port)
    if logged is None:
        note("   登录态: 查询失败（%s）" % href)
        return
    vidu = [n for n in names if n in ("JWT", "SSUID", "Shunt")]
    note("   页面: %s" % href)
    note("   登录: %s%s" % ("★未登录（缺 JWT）" if not logged else "已登录",
                          ("  凭证=" + ",".join(vidu)) if vidu else ""))
    if not logged:
        note("   ⇒ 请在这个窗口里登录一次 Vidu（手机验证码 / 扫码）。")


def cmd_status(a):
    v = cdp_alive(a.port)
    udd = active_udd(a)
    if not v:
        note("★ 端口 %d 无 CDP（实例未运行，或不是由本脚本启动的）" % a.port)
        note("   本渠道期望的 user-data-dir = %s（模式：%s）"
             % (udd, "junction" if a.junction else "独立目录"))
        for p in msedge_main_processes():
            note("   在跑的 Edge: pid=%s %s" % (p["pid"], p["cmdline"][:110]))
        return 1
    note("实例在运行：%s" % v.get("Browser"))
    if os.path.exists(STATE_FILE):
        try:
            note("留痕: %s" % json.load(open(STATE_FILE, encoding="utf-8")))
        except Exception:
            pass
    _report_login(a.port)
    return 0


def cmd_down(a):
    udd = active_udd(a)
    v = cdp_alive(a.port)
    if not v:
        note("端口 %d 无 CDP；改用优雅关闭处理本渠道实例（如果有）" % a.port)
        ours = [p for p in msedge_main_processes() if udd.lower() in p["cmdline"].lower()]
        if not ours:
            note("没有本渠道实例在跑。")
            return 0
        n, done = end_gracefully([p["pid"] for p in ours], quit_threads_too=True)
        note("已优雅处理 %d 个进程，%s" % (n, "已完成" if done else "★超时"))
        return 0 if done else 1
    try:
        from vidu_cdp import CDP
        with CDP(port=a.port, url_contains="") as c:
            c.send("Browser.close")
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < 45:
        if not cdp_alive(a.port):
            note("已优雅关闭（CDP Browser.close）—— cookie 已正常落盘。")
            return 0
        time.sleep(1)
    note("★ CDP 关闭超时，退回 WM_CLOSE（仍未强杀）")
    ours = [p for p in msedge_main_processes() if udd.lower() in p["cmdline"].lower()]
    n, done = end_gracefully([p["pid"] for p in ours], quit_threads_too=True) if ours else (0, True)
    note("已优雅处理 %d 个进程，%s" % (n, "已完成" if done else "★超时"))
    return 0 if done else 1


def cmd_accounts(a):
    """列出本机所有 Vidu 账号实例（一账号一实例一 profile）。"""
    accs = list_accounts()
    if not accs:
        note("还没有任何账号实例。用 `vidu_edge.py up --account 账号1` 创建。")
        return 0
    note("共 %d 个账号实例：" % len(accs))
    for x in accs:
        alive = "★运行中" if cdp_alive(x.get("port")) else "未运行"
        note("   %-12s 端口=%-6s pid=%-8s %s  起于 %s"
             % (x.get("account"), x.get("port"), x.get("pid"), alive, x.get("started_at", "")))
    return 0


def cmd_restart(a):
    cmd_down(a)
    time.sleep(2)
    return cmd_up(a)


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--account", default="账号1",
                        help="★ 账号名（一账号=一实例=一 profile）。如 账号1 / 账号2 / 张三")
    common.add_argument("--account-index", type=int, default=0,
                        help="账号序号（等价 `--account 账号N`）。★ 供**纯 ASCII** 的 .cmd 调用")
    common.add_argument("--port", type=int, default=0, help="0 = 按账号自动（9221+序号）")
    common.add_argument("--profile", default="Default")
    common.add_argument("--url", default=DEFAULT_URL)
    common.add_argument("--wait", type=int, default=60, help="等调试端口就绪的秒数")
    common.add_argument("--udd", default="",
                        help="显式 user-data-dir（默认=按账号推导 ~/.manju-vidu/profiles/<账号>）")
    common.add_argument("--junction", action="store_true",
                        help="改用 junction 指向真实 Edge Default profile（蹭现成登录态；"
                             "★ 会被 Edge「启动加速」预启动进程呛死，非必要别用）")
    common.add_argument("--close-existing", action="store_true",
                        help="仅 junction 模式有意义：优雅关掉占用同 profile 的其它 Edge")

    ap = argparse.ArgumentParser(description="Vidu 渠道浏览器实例入口（开/查/关/重启）",
                                 parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("up", cmd_up), ("status", cmd_status), ("down", cmd_down),
                     ("restart", cmd_restart), ("accounts", cmd_accounts)):
        p = sub.add_parser(name, parents=[common], help=name)
        p.set_defaults(fn=fn)
    a = ap.parse_args()
    if a.account_index:
        a.account = "账号%d" % a.account_index      # ★ .cmd 传序号，避免中文进 ASCII 文件
    if not a.port:
        a.port = account_of(a)[2]          # ★ 端口默认按账号推导
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
