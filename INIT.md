# INIT.md —— 一次性初始化（**正常开工不要读本文件**）

## ★★ 最优先：双击 `1-初始化环境.cmd` 就完事了

它自动做完下面全部工作，**不需要用户敲任何命令**：

| 步骤 | 做什么 |
|---|---|
| 1 | 找 Python（`py` → `python` → `python3`）；**找不到就用 winget 装 Python 3.13** |
|    | （winget 也没有 ⇒ 打开 python.org 下载页并提示"记得勾 Add python.exe to PATH"） |
| 2 | 装依赖 —— 只有一个：`websocket-client` |
| 3 | 检查 Microsoft Edge（没有则给出下载地址） |
| 4 | 检查浏览器实例与**登录态** |
| 5 | 在桌面建「**Vidu 账号N - 启动 / 关闭**」快捷方式 |

- ⚠ **装完 Python 后当前窗口看不到它，是 Windows PATH 的正常行为** —— 关掉重开再双击一次即可。
- ⚠ **本包只依赖 Python ＋ websocket-client ＋ Edge，⛔ 不需要 Node。**
- 等价命令（AI 可直接跑）：`python scripts/init_env.py [--account-index 1] [--no-pip] [--no-lnk]`

**本文件余下部分**讲的是 `vidu_edge.py` 背后的原理（为什么要独立 profile、CDP 怎么起），
**用户在正常使用中完全不需要读**；只有在**排障**（端口起不来 / 登录态丢失）时才往下看。

---

> 只在三种情况读它：**首次接入 / 换机器 / `doctor` 报未就绪**。
> 正常开工只读 `SKILL.md`，并先看机器状态文件：
> `C:\Users\Administrator\.manju-vidu\STATE.json`（很小，`verified: true` 就表示这台机器已配好）。
>
> ★ 机器级状态**只写 `~/.manju-vidu/`**，**绝不写进本 skill 目录** ——
> skill 目录会被升级/`git pull` 覆盖，`~/.manju-vidu/` 不会被碰。
>
> ★★ **本文件只管「把浏览器和登录态搞起来」**。平台本身的机制（页面为什么"不渲染"、
> 图片/音频怎么上传、mention 节点怎么落位、设置区每个控件怎么点、定位的坑）
> 全在 **`references/vidu-平台机制.md`** —— 那才是写自动化时要读的。

---

## 0. 一句话

Vidu 是**网页平台**，只能用 CDP 驱动浏览器操作。让 CDP 能连上的唯一门槛是：
**Edge 必须以「非默认 user-data-dir」启动并开 `--remote-debugging-port`**。

⚠ 上手自动化前先看 `references/vidu-平台机制.md` §0：**页面"卡在只渲染侧栏"不是坏了，
是 Chromium 节流后台标签**，`Page.bringToFront` 就好。

---

## 1. ★★ 为什么默认参数不管用（根因，别重复排查）

**现象**：`msedge.exe --remote-debugging-port=9222 --profile-directory=Default` 起来了、
进程命令行里**确实带这个参数**，但：

- 9222 端口不监听
- `<user-data-dir>\DevToolsActivePort` 文件不生成

**根因**：Chromium / Edge **136 起的安全加固** ——
**当 `--user-data-dir` 是默认目录时，`--remote-debugging-port` 被直接忽略**
（防止恶意程序附着到用户的默认 profile）。本机 Edge = `154.0.4258.53`，命中。

**错误结论（别走）**：「Edge 不支持远程调试」「被安全软件拦了」「端口被占用」「参数写错了」。

---

## 2. ★★ 解法：**独立 user-data-dir**（推荐，一次搞定的终极形态）

直接用**一个独立的、非默认路径**的目录当 `--user-data-dir`：

```
--user-data-dir=C:\Users\Administrator\.manju-vidu\vidu-profile --profile-directory=Default
```

- 路径不是默认路径 ⇒ 绕过 §1 的加固；
- **物理目录独立** ⇒ Chromium 的单例锁独立 ⇒ 用户的 Edge、
  Edge 的「启动加速」预启动进程**都干扰不到我们**（双保险，比 junction 强）。
- 代价：登录态/扩展不与用户日常 Edge 共享。**本机本来就没有 Vidu 登录态**，所以零损失。

### 2.1 ⛔ junction 方案是死结（2026-10-06 实测，别再走）

之前推荐的是「建 junction 指向真实 `Edge\User Data`」以保住登录态。**这条路走不通**：

- junction 让两个路径**指向同一个物理目录** ⇒ 和默认目录**共用同一把单例锁**；
- Edge 的**「启动加速」预启动进程**（`--no-startup-window`）会一直占着那把锁。
  它的特征是：
  - **没有任何可见窗口**，却挂着 crashpad / IME 这类**隐藏**句柄；
  - 我们的启动进程**只会把命令行转发给它然后退出** ⇒ 调试端口永远不监听；
  - 它有 **message-only 窗口**（`HWND_MESSAGE`），**`EnumWindows` 枚举不到** ⇒
    `WM_CLOSE` 够不着；给它所有线程发 `WM_QUIT`（实测 64 个线程）**也不退**。
- ⇒ 结论：**只要用 junction，就永远受它挟持。换独立目录即可彻底摆脱。**

★ 附带踩到的判据坑：判断「对方是真实会话还是预启动进程」时，
**必须只数"可见"顶层窗口**。只看窗口总数会被 crashpad/IME 的隐藏句柄骗过，
于是该补 WM_QUIT 时不敢补，进程永远退不掉（实测卡了一轮）。

---

## 3. ★ 开/关一律走 `vidu_edge.py`（别手敲 msedge 命令行）

`scripts/vidu_edge.py` 是**本渠道浏览器实例的唯一入口**，把上面所有坑焊在代码里：

```bash
PY="%USERPROFILE%/AppData/Local/Microsoft/WindowsApps/python.exe"
E="%USERPROFILE%/.workbuddy/skills/manju-vidu/scripts/vidu_edge.py"

$PY $E up        # 开（幂等：已在跑就只报状态）→ 末尾直接报"是否已登录"
$PY $E status    # 查：端口/留痕/登录态
$PY $E down      # 关：CDP Browser.close（优雅，cookie 正常落盘）
$PY $E restart   # 重启
```

它替我做了这些事：

| 关键动作 | 为什么 |
|---|---|
| **WMI `Win32_Process.Create`** 启动 | 会话内启动的 GUI 进程会被宿主回收（「开了又没了」）；Popen 兜底用 `CREATE_BREAKAWAY_FROM_JOB` |
| 默认用**独立目录**，`--junction` 才用真实 profile | 见 §2 |
| 启动前体检三分类：`ours` / `prelaunch` / `other` | 同 profile 只允许一个实例，冲突时新实例只会转发命令行、**不报错** |
| `down` 走 **CDP `Browser.close`** | 优雅关闭 ⇒ cookie 正常落盘。**绝不 `taskkill /F`** |
| 关别家进程只发 `WM_CLOSE`（=点 ×）；有可见窗口时**拒绝补 WM_QUIT** | 保护真实会话的 cookie 落盘 |
| 留痕 `~/.manju-vidu/edge-instance.json` + `vidu_edge.log` | 谁起的/何时/参数（红线：长任务必须留痕） |

★ 必须在**非沙箱**下运行（沙箱内连 `127.0.0.1` 都假性拒绝）。

---

## 4. 就绪判据

```bash
# 最省事：直接问工具（它自己会轮询 + 报登录态）
python scripts/vidu_edge.py up --wait 60
```

手测两处（★ 必须非沙箱；沙箱内恒为「积极拒绝」，是假失败）：

```python
urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://127.0.0.1:9222/json/version")
```

★ **本机 curl 走代理**，测 127.0.0.1 会拿到代理的报错文本 ⇒ **一律 urllib + `ProxyHandler({})` 直连**。
★ 别指望 `<user-data-dir>\DevToolsActivePort`：本机实测**两个目录都不生成**这个文件，
  但端口照样是通的 ⇒ **以 TCP 是否可连为准，不要以文件是否存在为准**（我为此误判过）。

---

## 5. ★ 登录态：怎么判、怎么保、怎么找

### 5.1 凭证长什么样（判登录**只看 JWT**）

| cookie | host | 特征 |
|---|---|---|
| `JWT` | `.service.vidu.cn` | **209 字节**，标准 JWT，载荷 `{iss:"iam", sub:"<Vidu用户id>", iat, exp}` |
| `SSUID` | `.service.vidu.cn` | 48 字节，明文尾部拼着同一个 user id |
| `Shunt` | `.service.vidu.cn` | 32 字节 |

- **有效期 = iat → exp 正好 15 天**，而且是**持久 cookie**（DB 里带 `expires_utc`），不是会话级。
  ⇒ 登录一次能撑半个月。
- **只看 `JWT` 在不在**。别的 vidu cookie（`cna` / `_clck` / `Hm_lvt` / `_uetvid` / `AGL_USER_ID`…）
  都是统计类，**登出状态照样有**，拿它们判登录必然误判。

### 5.2 ★★ 运行中的浏览器读不到 cookie 库（WinError 32，且 winerror 会丢）

Edge / Chromium **运行时把 `Default\Network\Cookies` 独占锁住**。
实测 `open(rb)` / `os.open(O_RDONLY)` / `shutil.copy` / `shutil.copy2` / ctypes `CreateFileW`
**全部失败**；Python 抛 `PermissionError [Errno 13]`，**且 `e.winerror is None`**
（CRT 把它映射成 EACCES 后丢掉了原始 32）——
⇒ **不能靠 `winerror == 32` 判断**，见到 `PermissionError` 就该当"库被占用"。

两条正确取数路径：

```python
# ① 最权威：走活浏览器内存（不碰磁盘）
#    vidu_session.py check / save   —— 内部 Network.getCookies
# ② 浏览器不想关时的旁路：VSS 卷影副本（需管理员）
#    PS: $cls=[WMIClass]"Win32_ShadowCopy"; $p=$cls.GetMethodParameters("Create")
#        $p["Volume"]="C:\\"; $p["Context"]="ClientAccessible"
#        $o=$cls.InvokeMethod("Create",$p,$null); $o["ReturnValue"]# 0=成功
#        Get-WmiObject Win32_ShadowCopy | ? { $_.ID -eq $o["ShadowID"] }  # 取 .DeviceObject
#    然后 Copy-Item "\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopyN\<path>" 出来
#    用完务必 $res.Delete()
```

⚠ 别把「磁盘读不到」当成「没登录」——`scan` 现在会把被占用的库**单独列出来报警**。

### 5.2b ★ 字节级打捞：证明"从没有过"而不是"被删了"

想区分「登录过但被清掉」和「从来没有」，可以用一个很硬的判据：

- SQLite 删除行只是把页放回空闲链表，**字节还在文件里**（vacuum 之前）；
- Chromium 的密文是 `v10` + 12B nonce + **AES-256-GCM**(明文+tag)，GCM 有认证标签
  ⇒ **解密成功就一定是真凭证**，几乎不可能误报。

做法：全文件扫 `v10`/`v11` 偏移 → 按各种密文长度用 profile 的 key 试解 → 看明文像不像 JWT。
若连 `JWT` / `.service.vidu.cn` 这些**明文列**的字节都不存在 ⇒ 该 profile **从未写入过** Vidu 凭证。
（Edge Default 实测：这些字符串各 0 次命中，而同文件 22 个其它网站密文全部解密成功 ⇒ 结论可靠。）

### 5.3 别弄丢

- ★ **关浏览器只用 CDP 优雅关闭**（`Browser.close`）或让它一直开着，
  **不要** `taskkill /F`、不要 `Stop-Process -Force`（初始化那一次除外）。
- 登录后立刻备份：`python scripts/vidu_session.py save --out ~/.manju-vidu/session.bak`。
- 掉了就：让**用户**在**已打开的那个 Edge 窗口**里登录一次（junction 方案下
  写的就是真实 Default profile，之后从任务栏正常开 Edge 也是登录态）。
- ★ 登录方式选**手机号验证码 / 扫码**，十几秒，不需要把密码交出来。

### 5.4 本机盘点（2026-10-06 用 VSS 逐库核过，**全过期 / 全没有**）

| 位置 | 账号(sub) | JWT 到期 | 结论 |
|---|---|---|---|
| `...\Roaming\360se6\User Data\Default` | `3101831139849427` | 2026-07-31 | 已过期 67 天 |
| `...\Local\Application Data\com.9tellect.creator\EBWebView\Default` | `3101842560175674` | 2026-01-16 | 已过期 263 天 |
| Edge 8 个 profile（Default / Profile2~Profile 8） | — | — | **一条 JWT 都没有** |
| `C:\EdgeDebugProfile\Default` | — | — | 只有 `AGL_USER_ID`/`NEXT_LOCALE`，**无 JWT** |
| `C:\EdgeProfiles\DeepSeek\Default` | — | — | 无 vidu cookie |
| Chrome(`C:\ChromeProfiles\Google1/2/3`)、Chrome 默认、Quark、Doubao | — | — | 无 vidu cookie |

★ 而且**连被删行的残字都没有**：对 Edge Default 的 cookie 库做全量字节打捞
（`.service.vidu.cn` / `JWT` / `SSUID` / `Shunt` 各 0 次命中；同一文件里 22 个其它网站的
`v10` 密文全部成功解密，证明密钥与方法都对）⇒ **那里从来没有写入过 Vidu 凭证**，
不是"登录过又被删"。

⇒ **本机不存在任何可复用的有效 Vidu 登录态**，用一次登录换来 15 天。`scan` 可复现该结论。

---

## 6. 常见故障速查

| 现象 | 真因 | 处置 |
|---|---|---|
| 端口不监听、`DevToolsActivePort` 不存在 | 用了默认 user-data-dir（加固） | 用 §2 **独立目录** |
| 沙箱内测端口「积极拒绝」 | 沙箱网络隔离（**假失败**） | 非沙箱重测 |
| `curl` 报 `upstream connect failed` | curl 走代理 | 换 urllib + `ProxyHandler({})` |
| Edge 启动几秒后消失 | 会话清理了子进程 | 走 `vidu_edge.py up`（内部用 WMI） |
| **启动进程活着但端口不通、或启动进程秒退** | 命令行被**转发**给占着同 profile 的实例（常见元凶＝「启动加速」预启动进程） | **改用独立目录**（别用 junction） |
| 有进程但 `EnumWindows` 数到 0 个窗口 | 对方是 message-only 窗口 / 纯预启动 | 只数**可见**窗口来判类别（§2.1 坑） |
| 页面显示「登录」按钮、模型只有 Q3 | 掉登录（或本就没登） | 用户在窗口里登录一次（§5.3） |
| 磁盘读 cookie 报 `PermissionError`、`winerror=None` | 浏览器独占锁（§5.2） | 走 CDP，或 VSS |
| `scan` 报「库被占用」 | 同上，正常现象 | 看 `check` 的 CDP 结果 |
| Bash 里调 PowerShell 被拒 | 安全策略 | 用 PowerShell 工具 |
| PowerShell 工具不返回 stdout | 本机限制 | `Out-File` 写文件再 Read |
| 磁盘上有库但"扫不到 vidu" | 可能静默跳过 | 见 §5.2：`PermissionError` ≠ 没有 |
