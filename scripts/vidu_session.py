# -*- coding: utf-8 -*-
"""
vidu_session.py —— Vidu 登录态的「定位 / 检查 / 备份 / 恢复」。

背景（2026-10-06 踩出来的）：
  Vidu 的登录凭证不是普通 cookie，而是 **`.service.vidu.cn` 域下的
  `JWT` + `SSUID` + `Shunt`** 三个加密 cookie（Chromium `v10` = DPAPI key + AES-256-GCM）。
  ⇒ 判断"某浏览器有没有登 Vidu"，**只看这三个 cookie 在不在**，别看别的。
  ⇒ 找登录态时不要靠肉眼翻浏览器：用本脚本的 `scan` 一次扫完所有 Chromium 系 profile，
    并把 JWT 的 exp 解出来（能立刻分辨"有凭证但已过期"）。

子命令：
  scan                 扫描本机所有 Chromium 系 user-data-dir，报告 vidu 凭证与有效期
  check                检查 Edge(CDP) 当前会话是否已登录（含 localStorage 深度探测）
  snapshot --out <db>  字节级备份 cookie 库（★ 需要 Edge 已关闭；最可靠）
  restore  --in  <db>  把备份里的 vidu 行**原样搬回** cookie 库（★ 需要 Edge 已关闭）
  save  --out <file>   走 CDP 把当前凭证存成 DPAPI 加密文件（Edge 可开着；见注意事项）
  load  --in  <file>   把 DPAPI 备份注入 Edge（用于换 profile / 重装后免登录）

备份策略（★ 优先 snapshot，而不是 save）：
  `snapshot` 直接复制 SQLite 库，恢复时用 ATTACH 把 vidu 行**原样搬回** ——
  encrypted_value 逐字节不变，不受"密文是二进制、CDP 传字符串会被破坏"的影响。
  `save` 走 CDP `Network.getCookies`，`JWT` 是纯文本没问题，
  但 `SSUID` / `Shunt` 的明文含非 UTF-8 字节，跨 CDP 往返有风险 ⇒ **仅作旁路**。

注意：save/snapshot 的内容都是**登录凭证**。save 已用 Windows DPAPI（当前用户）加密，
文件只能在这台机器上用；snapshot 是原始密文库，同样只对本机同一个 Windows 用户有效。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

import win32crypt

VIDU_COOKIE_NAMES = ("JWT", "SSUID", "Shunt")
VIDU_HOST_LIKE = "%vidu%"

# 本机默认：**独立自动化 profile**（与 vidu_edge.py 保持一致）
DEFAULT_UDD = r"C:\Users\Administrator\.manju-vidu\vidu-profile"
DEFAULT_PROFILE = "Default"


def _cookies_path(udd, profile):
    return os.path.join(udd, profile, "Network", "Cookies")


# ---------------- Chromium cookie 解密 ----------------
def _aes_key(user_data_dir: str) -> bytes:
    st = json.load(open(os.path.join(user_data_dir, "Local State"), encoding="utf-8"))
    enc = base64.b64decode(st["os_crypt"]["encrypted_key"])
    if enc[:5] != b"DPAPI":
        raise RuntimeError("encrypted_key 不是 DPAPI 前缀: %r" % enc[:8])
    return win32crypt.CryptUnprotectData(enc[5:], None, None, None, 0)[1]


def _decrypt(value: bytes, key: bytes) -> str:
    v = bytes(value)
    if v[:3] in (b"v10", b"v11"):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(v[3:15], v[15:], None).decode("utf-8", "replace")
    if v[:3] == b"v20":
        raise RuntimeError("v20 app-bound 加密，需 Elevation Service，本脚本不支持")
    return win32crypt.CryptUnprotectData(v, None, None, None, 0)[1].decode("utf-8", "replace")


LOCKED = object()


def _snapshot_db(src: str, dst: str):
    """把被浏览器占用的 sqlite 库复制出来。

    ⚠ 不能用 shutil.copy2 / CopyFile2，也不能用普通 open()：
    **Edge / Chromium 运行时会把 Cookies 库以「不允许共享」方式独占**
    （实测 `open(rb)` / `os.open(O_RDONLY)` / ctypes CreateFileW 全部
    `WinError 32 ERROR_SHARING_VIOLATION`）。⇒ **浏览器在跑的时候，磁盘上读不到新 cookie**，
    唯一权威来源是 CDP `Network.getCookies`（见本脚本 check / save）。

    返回 True=成功；LOCKED=库被占用（浏览器在跑）；False=其它失败。
    """
    try:
        with open(src, "rb") as f:
            data = f.read()
    except PermissionError:
        # ⚠ 本机的 PermissionError.winerror 是 None（CRT 把 ERROR_SHARING_VIOLATION
        #   映射成 EACCES 且丢掉 winerror）⇒ 不能靠 winerror==32 判断，见 PermissionError 即认定被占用。
        return LOCKED
    except OSError:
        return False
    with open(dst, "wb") as f:
        f.write(data)
    for suf in ("-journal", "-wal", "-shm"):
        if os.path.exists(src + suf):
            try:
                with open(src + suf, "rb") as f:
                    sd = f.read()
                with open(dst + suf, "wb") as f:
                    f.write(sd)
            except OSError:
                pass
    return True


def _read_cookies(user_data_dir: str, profile: str, names=None, host_like=None):
    """返回 [(host_key, name, value, expires_utc)]；value 为解密后字符串。
    若库被占用（浏览器在跑）返回 LOCKED —— 别把"读不到"当成"没有"。"""
    ck = os.path.join(user_data_dir, profile, "Network", "Cookies")
    if not os.path.exists(ck):
        return []
    tmp = os.path.join(tempfile.gettempdir(), "vidu_sess_%d.db" % os.getpid())
    out = []
    try:
        snap = _snapshot_db(ck, tmp)
        if snap is LOCKED:
            return LOCKED
        if snap is not True:
            return []
        con = sqlite3.connect(tmp)
        cur = con.cursor()
        where, args = [], []
        if names:
            where.append("name IN (%s)" % ",".join("?" * len(names)))
            args += list(names)
        if host_like:
            where.append("host_key LIKE ?")
            args.append(host_like)
        sql = "SELECT host_key,name,encrypted_value,expires_utc FROM cookies"
        if where:
            sql += " WHERE " + " AND ".join(where)
        cur.execute(sql, args)
        key = None
        for h, n, v, eu in cur.fetchall():
            try:
                if key is None:
                    key = _aes_key(user_data_dir)
                out.append((h, n, _decrypt(v, key), eu))
            except Exception as e:
                out.append((h, n, "<解密失败:%s>" % type(e).__name__, eu))
        con.close()
    finally:
        for suf in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(tmp + suf)
            except OSError:
                pass
    return out


def _jwt_exp(value: str):
    """从 JWT 里取 exp（unix秒）；不是 JWT 返回 None。"""
    if value.count(".") != 2:
        return None
    p = value.split(".")[1]
    p += "=" * (-len(p) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(p)).get("exp")
    except Exception:
        return None


def _fmt_exp(exp):
    if not exp:
        return "无 exp"
    left = exp - datetime.now().timestamp()
    tag = "有效" if left > 0 else "★已过期"
    return "%s (%s %.1f 天)" % (datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M"), tag, abs(left) / 86400)


# ---------------- 子命令 ----------------
def cmd_scan(a):
    """扫描所有 Chromium 系 user-data-dir。"""
    roots = ["C:\\Users\\Administrator\\AppData", "C:\\", "D:\\", "E:\\", "F:\\"]
    seen, results, locked_browsers = set(), [], []
    skip = {"Windows", "$Recycle.Bin", "Program Files", "Program Files (x86)", "ProgramData",
            "System Volume Information", "node_modules", ".git"}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath[len(root):].count(os.sep) > 4:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith("$")]
            if "Local State" not in filenames:
                continue
            rp = os.path.realpath(dirpath)
            if rp in seen:
                continue
            seen.add(rp)
            locked = []
            for prof in os.listdir(dirpath):
                if not (prof == "Default" or prof.startswith("Profile")):
                    continue
                rows = _read_cookies(dirpath, prof, names=VIDU_COOKIE_NAMES)
                if rows is LOCKED:
                    locked.append(prof)
                    continue
                if not rows:
                    continue
                jwt = next((v for h, n, v, e in rows if n == "JWT"), None)
                results.append({"path": dirpath, "profile": prof,
                                "cookies": [n for h, n, v, e in rows],
                                "jwt_exp": _jwt_exp(jwt) if jwt else None})
            if locked:
                locked_browsers.append({"path": dirpath, "profiles": locked})
    if locked_browsers:
        print("\n⚠ 以下浏览器的 cookie 库正被占用（进程在跑），磁盘读不到：")
        for x in locked_browsers:
            print("   %s   [%s]" % (x["path"], ", ".join(x["profiles"])))
        print("   ⇒ 这类只能用 CDP 读：`vidu_session.py check`（走活浏览器内存，最权威）")
    if not results:
        print("\n★ 磁盘上没有任何 Chromium 系 profile 存着 vidu 登录凭证 (JWT/SSUID/Shunt)")
    for r in results:
        print("\n  %s  [%s]" % (r["path"], r["profile"]))
        print("    cookies: %s" % r["cookies"])
        print("    JWT exp: %s" % _fmt_exp(r["jwt_exp"]))
    if a.out:
        json.dump({"found": results, "locked": locked_browsers},
                  open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写入:", a.out)
    return 0


def _cdp(port):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from vidu_cdp import CDP
    return CDP(port=port, url_contains="vidu.cn")


_DEEP_JS = """(() => {
  const trim = (s, n) => { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n) + '…len=' + s.length : s; };
  const dump = (st) => { const o = {}; try { for (let i = 0; i < st.length; i++) { const k = st.key(i); o[k] = trim(st.getItem(k), 50); } } catch (e) { o['<err>'] = String(e); } return o; };
  return {
    href: location.href,
    ls: dump(localStorage),
    ss: dump(sessionStorage),
    txt: trim(document.body ? document.body.innerText : '', 300)
  };
})()"""


def cmd_check(a):
    with _cdp(a.port) as c:
        ck = c.send("Network.getCookies",
                    {"urls": ["https://www.vidu.cn", "https://service.vidu.cn", "https://vidu.cn"]})
        names = {x["name"] for x in ck.get("cookies", [])}
        have = sorted(names & set(VIDU_COOKIE_NAMES))
        logged = "JWT" in names
        print("vidu cookie 名:", sorted(names))
        print("登录凭证:", have or "无")
        print("结论:", "已登录" if logged else "★未登录（缺 JWT）")
        # 深挖：有的站点把凭证放 localStorage，只看 cookie 会假阴性
        d = c.eval(_DEEP_JS) or {}
        print("页面:", d.get("href"))
        ls = d.get("ls") or {}
        print("localStorage (%d 键):" % len(ls))
        for k, v in list(ls.items())[:40]:
            print("   %-28s = %s" % (k, v))
        ss = d.get("ss") or {}
        if ss:
            print("sessionStorage (%d 键):" % len(ss))
            for k, v in list(ss.items())[:20]:
                print("   %-28s = %s" % (k, v))
        print("页面文字前 300 字:", (d.get("txt") or "").replace("\n", " | "))
        return logged


def cmd_save(a):
    with _cdp(a.port) as c:
        ck = c.send("Network.getCookies",
                    {"urls": ["https://www.vidu.cn", "https://service.vidu.cn", "https://vidu.cn"]})
        keep = [x for x in ck.get("cookies", []) if x["name"] in VIDU_COOKIE_NAMES]
        if not any(x["name"] == "JWT" for x in keep):
            print("★ 当前未登录（没有 JWT），不备份"); return 1
        blob = json.dumps({"saved_at": datetime.now().isoformat(), "cookies": keep},
                          ensure_ascii=False).encode("utf-8")
        enc = win32crypt.CryptProtectData(blob, "manju-vidu-session", None, None, None, 0)
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        open(a.out, "wb").write(enc)
        print("已备份 %d 个凭证 -> %s（DPAPI 加密，仅本机当前用户可解）" % (len(keep), a.out))
        print("  含:", [x["name"] for x in keep])
        print("  ⚠ 这是**旁路**备份：JWT 是纯文本没问题，但 SSUID/Shunt 明文含非 UTF-8 字节，")
        print("    跨 CDP 往返可能失真。要 100% 可靠请用 `snapshot`（需先优雅关闭 Edge）。")
    return 0


def cmd_load(a):
    enc = open(a.infile, "rb").read()
    data = json.loads(win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1].decode("utf-8"))
    with _cdp(a.port) as c:
        ok = 0
        for x in data["cookies"]:
            r = c.send("Network.setCookie", {"name": x["name"], "value": x["value"],
                                             "domain": x["domain"], "path": x.get("path", "/"),
                                             "secure": x.get("secure", True),
                                             "httpOnly": x.get("httpOnly", True)})
            ok += 1 if r.get("success") else 0
        print("注入 %d/%d 个凭证（备份时间 %s）" % (ok, len(data["cookies"]), data.get("saved_at")))
        c.send("Page.reload", {"ignoreCache": True})
        print("已刷新页面，请用 check 复核")
    return 0


def _summarize_db(path, key_dir=None):
    """读一个（已复制出来的）cookie 库，报告 vidu 行。返回 (rows, junk)。"""
    try:
        con = sqlite3.connect(path)
        cur = con.cursor()
        cur.execute("SELECT host_key,name,encrypted_value,expires_utc FROM cookies WHERE host_key LIKE ?",
                    (VIDU_HOST_LIKE,))
        raw = cur.fetchall()
        con.close()
    except Exception as e:
        return None, "打不开: %s" % e
    key = None
    if key_dir:
        try:
            key = _aes_key(key_dir)
        except Exception:
            key = None
    out = []
    for h, n, v, eu in raw:
        val = None
        if key is not None:
            try:
                val = _decrypt(v, key)
            except Exception:
                val = None
        out.append({"host": h, "name": n, "expires_utc": eu, "value": val})
    return out, None


def _vss_snapshot_file(src_file: str, dst_file: str):
    """用 VSS 卷影副本读一个**被浏览器独占**的文件。

    为什么需要它：运行中的 Chromium 把 Cookies 库锁死（见 §5.2），
    但我们又不想为了备份而关掉浏览器（那是登录态的命根子）。
    VSS 在块层做快照 ⇒ 绕过文件锁，**不用关浏览器**。
    需要管理员权限。

    返回 (ok, msg)。
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError as e:
        return False, "缺 pywin32: %s" % e
    pythoncom.CoInitialize()
    svc = win32com.client.Dispatch("WbemScripting.SWbemLocator").ConnectServer(".", "root\\cimv2")
    cls = svc.Get("Win32_ShadowCopy")
    inp = cls.Methods_("Create").InParameters.SpawnInstance_()
    inp.Properties_.Item("Volume").Value = os.path.splitdrive(src_file)[0] + "\\"
    inp.Properties_.Item("Context").Value = "ClientAccessible"
    out = svc.ExecMethod("Win32_ShadowCopy", "Create", inp)
    rc = out.Properties_.Item("ReturnValue").Value
    sid = out.Properties_.Item("ShadowID").Value
    if rc != 0:
        return False, "VSS 创建失败 rc=%s（多半是没管理员权限）" % rc
    dev = None
    for s in svc.ExecQuery("SELECT * FROM Win32_ShadowCopy WHERE ID='%s'" % sid):
        dev = s.DeviceObject
    if not dev:
        return False, "找不到卷影副本设备"
    rel = os.path.splitdrive(src_file)[1].lstrip("\\")
    shadow_path = dev + "\\" + rel
    try:
        with open(shadow_path, "rb") as f:
            data = f.read()
        os.makedirs(os.path.dirname(os.path.abspath(dst_file)), exist_ok=True)
        with open(dst_file, "wb") as f:
            f.write(data)
    except OSError as e:
        return False, "从卷影副本复制失败: %s" % e
    finally:
        try:
            for s in svc.ExecQuery("SELECT * FROM Win32_ShadowCopy WHERE ID='%s'" % sid):
                s.Delete_()
        except Exception:
            pass
    
    return True, "VSS 快照成功（%d bytes）" % os.path.getsize(dst_file)


def cmd_snapshot(a):
    src = _cookies_path(a.user_data_dir, a.profile)
    if not os.path.exists(src):
        print("★ 找不到 cookie 库: %s" % src)
        return 1
    out = os.path.abspath(a.out)
    if a.via_vss:
        ok, msg = _vss_snapshot_file(src, out)
        print(("已快照 -> " if ok else "★ ") + msg)
        if not ok:
            return 1
    else:
        if os.path.dirname(out):
            os.makedirs(os.path.dirname(out), exist_ok=True)
        r = _snapshot_db(src, out)
        if r is LOCKED:
            print("★ cookie 库被占用 —— Edge 正在运行，直接复制拿不到。")
            print("  ① 加 `--via-vss`（VSS 卷影副本，**不用关浏览器**，需管理员）；")
            print("  ② 先用 CDP 优雅关闭 Edge（`vidu_edge.py down`）再快照；")
            print("  ③ 旁路：`save --out <file>` 走 CDP 拿 JWT。")
            return 1
        if r is not True:
            print("★ 快照失败（非占用原因）: %s" % src)
            return 1
        print("已快照 -> %s (%d bytes)" % (out, os.path.getsize(out)))
    rows, err = _summarize_db(out, key_dir=a.user_data_dir)
    if err:
        print("   ⚠ 复核失败: %s" % err)
    elif not rows:
        print("   ⚠ 快照里没有任何 vidu cookie —— 这份 profile 现在可能是未登录状态。")
    else:
        print("   含 vidu cookie %d 条:" % len(rows))
        for x in rows:
            tag = ""
            if x["name"] == "JWT":
                tag = ("  <<< " + _fmt_exp(_jwt_exp(x["value"] or ""))) if x["value"] else "  <<< ⚠解密失败"
            print("     %-8s %-24s %s" % (x["name"], x["host"], tag))
    return 0


def cmd_restore(a):
    bak = os.path.abspath(a.infile)
    if not os.path.exists(bak):
        print("★ 备份不存在: %s" % bak)
        return 1
    src = _cookies_path(a.user_data_dir, a.profile)
    if not os.path.exists(src):
        print("★ 找不到 cookie 库: %s" % src)
        return 1
    probe = os.path.join(tempfile.gettempdir(), "vidu_probe_%d.db" % os.getpid())
    try:
        r = _snapshot_db(src, probe)
    finally:
        pass
    for suf in ("", "-journal", "-wal", "-shm"):
        try:
            os.remove(probe + suf)
        except OSError:
            pass
    if r is LOCKED:
        print("★ cookie 库被占用 —— 请先**优雅关闭 Edge** 再恢复（写库时浏览器不能开着）。")
        return 1
    if r is not True:
        print("★ 探测失败（非占用原因），中止以免写坏库。")
        return 1
    con = sqlite3.connect(src)
    try:
        con.execute("ATTACH DATABASE ? AS bak", (bak,))
        con.execute("DELETE FROM cookies WHERE host_key LIKE ?", (VIDU_HOST_LIKE,))
        cur = con.execute("INSERT INTO cookies SELECT * FROM bak.cookies WHERE host_key LIKE ?",
                          (VIDU_HOST_LIKE,))
        n = cur.rowcount
        con.commit()
        con.execute("DETACH DATABASE bak")
    except Exception as e:
        con.rollback()
        print("★ 恢复失败，未改动: %s: %s" % (type(e).__name__, e))
        return 1
    finally:
        con.close()
    print("已把 %d 条 vidu cookie 从 %s 原样搬回 %s" % (n, bak, src))
    print("⇒ 现在启动 Edge 即应为登录态；跑 `check` 复核。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--user-data-dir", dest="user_data_dir", default=DEFAULT_UDD)
    ap.add_argument("--profile", dest="profile", default=DEFAULT_PROFILE)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan"); p.add_argument("--out", default=""); p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("check"); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("snapshot")
    p.add_argument("--out", required=True)
    p.add_argument("--via-vss", action="store_true",
                   help="用 VSS 卷影副本读被占用的库（★ 不用关浏览器，需管理员权限）")
    p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("restore"); p.add_argument("--in", dest="infile", required=True); p.set_defaults(fn=cmd_restore)
    p = sub.add_parser("save"); p.add_argument("--out", required=True); p.set_defaults(fn=cmd_save)
    p = sub.add_parser("load"); p.add_argument("--in", dest="infile", required=True); p.set_defaults(fn=cmd_load)
    a = ap.parse_args()
    rc = a.fn(a)
    sys.exit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    main()
