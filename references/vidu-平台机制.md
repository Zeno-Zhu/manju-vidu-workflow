# Vidu 平台机制（2026-10-07 实测，权威）

<!-- (c) manju-vidu-workflow · MJVID:28d4b3c9b6 · 溯源标记请勿删除 -->

> 本文件记录**从平台实测出来的硬事实**。与脚本里的注释互为冗余，改脚本时同步改这里。
> 渠道 = `https://www.vidu.cn/create/character2video`（参考生视频）。

## 0. 页面渲染：★★ 先看这条，能省一小时

**症状**：`document.body.innerText.length == 38`（只剩侧栏 `首页/资产/创作/S/Agent/工具/849/标准版/创作者/计划/API`）、
`input[type=file]` 数量 0、`.tiptap.ProseMirror` 不存在 —— 看起来"页面挂了"。

**真相**：**Chromium 节流后台标签**。创作页主内容区依赖 rAF / 尺寸测量才挂载，标签不在前台就不渲染。

**判据（别再看 innerText 就下结论）**：
```
document.body.innerHTML.length        → 464580      ← 内容一直在！
document.querySelectorAll('*').length → 1121
document.body.innerHTML.includes('上传') → true
```

**修复**：见本文件末尾「**窗口不抢焦点**」节 —— ⛔ 已**不再**使用 `Page.bringToFront`（它会抢焦点）。
脚本里还加了 `Emulation.setFocusEmulationEnabled({enabled:true})` 兜底。

⚠ **旧记载「`Page.navigate` 同 URL 会让 SPA 卡住，要用 `Page.reload`」是误判，已作废。**
`Page.reload` 当年"看起来有效"只是因为 reload 顺带把标签激活了。**刷不刷新与渲染无关。**

## 1. 素材上传

### 图片
- DOM 里**真实存在**：`input[type=file][multiple]`，`accept="image/jpeg,image/png,image/webp"`。
- 投喂：`DOM.setFileInputFiles({objectId, files:[...]})`
  ★ **必须用 `objectId`**（SPA 重渲染会让 `nodeId` 失效，报 `Could not find node with given id`）。
- 上传后平台自动命名 **`图1`/`图2`…（按上传顺序）**，并**自动往编辑器插 mention 节点**（默认堆在末尾）。

### 音频
- ★★ **DOM 里查不到**（`querySelectorAll('input[type=file]')` 只有图片那个）。
  点「+ 音频」是**真的去唤起原生文件选择框**。
- 正解四步：
  1. `Page.setInterceptFileChooserDialog({enabled:true})`
  2. 用 `Input.dispatchMouseEvent`（mousePressed/mouseReleased）**真实点**「音频」按钮
  3. 收 `Page.fileChooserOpened` 事件 → 拿 **`backendNodeId`**
  4. `DOM.setFileInputFiles({backendNodeId, files:[...]})`
- 隐藏 input 真实属性：`accept=".wav,.mp3,.aiff,.aif,.aac,.ogg,.flac"`、`multiple`、`style="display:none"`。
- 上传后命名 **`音频1`/`音频2`…**。
- ★★ **音频上限 = 3**。平台自己弹红字「**一次最多选择 3 个文件，超出部分已忽略。**」
  实测传 4 个只生成 `音频1/2/3`。**官方限制，改不了。**

### 主体库
- 「主体」行 = 主体库，**本项目不使用**。

## 1.5 ★★ 提交（`POST /vidu/v1/tasks`）—— 只有网络层知道成没成

### 请求形态（实测抓包）

```jsonc
POST https://service.vidu.cn/vidu/v1/tasks
{"input":{"prompts":[{"type":"text","content":"【基础信息】…[@图1]…[@音频1]…"}]},
 "model":"…","settings":{"duration":15,"aspect_ratio":"9:16","resolution":"1080p",
                          "codec":"h265","sample_count":1,"model_version":"3.4", ...}}
```

- mention 在报文里序列化成 **`[@图N]` / `[@音频N]`** 的文本内联标记。
- 提交前平台先调 `GET /vidu/v1/tasks/credits?...` 校验；内测期返回
  `{"cost_credits":0,"can_submit":true,"current_credits":849}`。

### ★★ 两个必踩的坑

1. **按钮 `aria-disabled=true` 时点击静默无效**。
   改完**宽高比**等设置会触发积分校验，校验期间按钮被禁用；
   此时用真实鼠标点下去 → HTTP 200、**零 POST**、页面无报错、表单也不 reset。
   **运行日志里的表现**：`{"click":{"ok":true,"ariaDisabled":"true"}}` 且**没有** `verified:true`。
   ⇒ 必须 **等 `aria-disabled` 变 `null` 再点 + 网络确认 + 重试**。
2. **提交成功后表单不清空** ⇒ 没有任何 DOM 标记能判成功。
   ⇒ 只能靠**旁路 CDP 连接监听 `POST /vidu/v1/tasks`**（`vidu_run.NetWatch`）。
3. 按钮选择器用 ★ **`[data-testid=form-submit-button]`**。
   按文本 `startsWith('创作')` 会被右栏历史卡片的「**继续创作**」等干扰。

### 核验"真的排上了、且没被截断"

任务卡上的「**重新编辑**」会把该任务的内容读回编辑器 ⇒ 直接量：

```
长度 3303 ｜ 字节 9051 ｜ mention 10  （与提交前逐字一致 ⇒ 未被截断）
```

### 提示词长度的真相

- 前端控制台会打印 ★ `params: prompt can not over 1024 byte, please check;`（每次提交 2 条）。
- **这是旧版软告警，不拦提交**：实测 3303 字照常 200、照常排队、读回一字不少。
- 我们自己的代码里**没有**这个串（全库 grep 无匹配）⇒ 不是项目侧限制。
- 官方口径：FAQ 说 Web 端 **~1500 字符**；API `text2video` 文档说 **≤5000 字符**。
  ⇒ **不要为这条告警去砍提示词**。

### 任务状态词

网页右栏任务卡会出现 `排队中 → 生成中 → 已完成 / 失败`。
任务卡 `.rect` 可用 `document.querySelectorAll` + 日期文本定位（见 `vidu_verify.py`）。

## 1.6 ★★ 任务历史与无水印下载（`GET /vidu/v1/tasks/history/me`）

### 列表接口

```
GET https://service.vidu.cn/vidu/v1/tasks/history/me?page_size=100
→ {"total": 0, "tasks": [ … ], "next_page_token": {"created_at": "2026-10-03T08:29:27.178449Z"}}
```

- `total` **恒为 0**（未用）；**每页最多 100 条**。
- ★★ **翻页参数（2026-10-13 实测修正，旧记载是错的）**：用**页码式**
  `?pager.pagesz=100&pager.page=N`。
  · `?page_size=200/500` 一律只回 **100**（上限 100）；
  · `?page=2` / `?page_num=2` / `?offset=100` **全部无效**（返回第 1 页）；
  · ⚠ **只写 `pager.page=N` 而没写 `pager.pagesz`** ⇒ 每页只有 **10** 条（默认页大小 10）；
  · `next_page_token.created_at` 游标**能返回但不可靠**，别依赖。
- ★★ **必须翻页**：老剧的组会被新任务挤出第 1 页 ⇒ 不翻页会**整批报「台账里没有该组」**，
  看起来像"任务丢了"（本项目实测：第 1 页 100 条只命中 0 组；翻到 1981 条才全命中）。
- 在页面上下文里 `fetch(url, {credentials:'include'})` 即可复用登录态。
  ⚠ 后台标签会冻结 promise ⇒ 已在**启动参数**层面取消节流（见文末节），⛔ 不要用 `Page.bringToFront`。
- ⚠ 返回体**很大**（100 条 ≈ 770 KB，因为含每条的**完整提示词原文**）⇒ 取回后在 JS 侧就裁成需要的字段。

### 任务对象（实测字段）

| 字段 | 说明 |
|---|---|
| `id` | ★ 平台任务 id，如 `3498331081384204` |
| `state` | `created / preparation / processing / success / failed` |
| `created_at` | 提交时刻（ISO，UTC） |
| `input.prompts[0].content` | ★ **提交的提示词原文** ⇒ 含唯一标记 `分镜组ID：<组id>` |
| `settings` | `duration / aspect_ratio / resolution / model_version(3.4) / codec / sample_count` |
| `err_code` / `err_msg` | 失败原因 |
| `creations[0].uri` | `…/segment-00/`**`watermarked`**`.mp4` ← 有水印 |
| `creations[0].download_uri` | `…/segment-00/`**`video`**`.mp4` ← ★ **无水印** |
| `creations[0].nomark_uri` | 同上（等价） |
| `creations[0].cover_uri` | 封面 jpeg |
| `creations[0].video` | `{duration, fps, resolution:{width,height}, ratio}` |
| `creations[0].has_audio` | 有无音轨 |

### ★★ 组 ↔ 任务 怎么对上

**锚点 = 提示词原文里的 `分镜组ID：<组id>`**（我们写词时第一段就带）。不靠提交顺序、不靠时间、不靠标题。
同一组多次提交 ⇒ **取最新 `success`**；没有 success 就退最新一条并标注状态。

### ★★ 下载不需要 cookie

`download_uri` 自带 `auth_key=…` **签名**（形如 `?auth_key=<epoch>-0-0-<md5>&response-content-disposition=attachment;filename=vidu-video-<creation_id>.mp4`）
⇒ **裸 HTTP GET 就能下**，不必在浏览器里点。

实测：`1_1_标准` / `1_2_标准` 各下 5.9 / 5.4 MB，`ffprobe` 校验 **1080×1920 / HEVC + AAC / 15.04s**，
抽帧目检**无水印、无 Logo、无角标**。

### 落盘

```text
<项目>/02_分集工程/EP01/<组id>_vidu.mp4        ← 成片（★ 用户 2026-10-07：全部下到 EP01）
<项目>/02_分集工程/EP01/<组id>_vidu_cover.jpg  ← 封面
<项目>/_出片记录/_vidu_tasks.json ＋ ~/.manju-vidu/tasks.json   ← 台账
```

★ 后缀 `_vidu` **必须保留** —— 云鲸的同名成片躺在 `02_分集工程/` **根下**（如 `1_1_标准.mp4`）。

### ⚠ `success` 但 URL 还是空的（中间态）

实测 `5_1_标准` 的任务 `3498341778550759`：第一次拉台账时 `state=success`、`creations` 有 1 条，
但 `uri` / `download_uri` 都是**空串**；**几分钟后同一任务再拉就有地址了**。
⇒ 平台**先置 `success`、稍后才挂文件**。对策：`pick_for_group` 在 success 里
**优先取带地址的**，遇到"无成片地址"先**重跑一次 `list`**。

### ⚠ Q4 内测期排队很慢

首日实测：53 组提交完 ⇒ 只有 2 组 `success`、2 组 `processing`、2 组 `preparation`、
**47 组长期停在 `created`**；另有 1 组（无音色的 `2_1_标准`）被判 `failed`。
⇒ 下载要**反复跑**（每次先 `list` 刷新台账）；**没有成片 ≠ 失败，别重提交**（会重复计费）。

## 2. mention 节点（`名称=@` 写法的落位机制）

- tiptap 实例挂在 DOM 上：`document.querySelector('.tiptap.ProseMirror').editor.view`
  → 直接拿 `EditorView`（不用翻 React fiber）。
- `state.schema.nodes` = `['paragraph','mention','doc','text']` ⇒ schema 里有 `mention`。
- 节点结构：`{type:'mention', attrs:{id, label, color:'#999BAC', preview:'blob:…', mediaType:'image'|'audio'}}`
  - `label` = `图N` / `音频N`（平台命名）
  - `id` = 绑定用 UUID（**沿用即可保持绑定**）
- ★★ **不要搬 DOM**（会与 ProseMirror 文档模型脱同步，看着对、提交时丢）。
  正解：**重建整篇文档** —— `ed.commands.setContent({type:'doc', content:[...]}, true)`，
  mention 可放任意位置。
- `pnN` 与 `图N` **同序**：`pn1,二丫少女=@` → 文本 `pn1,二丫少女=` + `mention(图1)`。
- ★ **`vmN` 与 `音频N` 同序**（2026-10-07 补测）：`vm1,中年男性｜强势严厉01（=参考音频1，用于王叔）=@`
  → 文本 `vm1,…（=参考音频1，用于王叔）=` + `mention(音频1)`。
  ⚠ **旧版 `build_blocks` 只认 `pn`** ⇒ 音频 mention 被当"没引用的资产"堆到末尾、
  且 `vmN,…=@` 整行**原样残留在正文里**（含 `=@`）。**已修**：两类都放回原位。
- ★ 解析用 **`=@` 当唯一终止符**（`(pn|vm)(\d+),(.*?)=\s*@`，惰性）：
  名称里可能含 `（=参考音频1，用于王叔）` 这种带 `=`/`，` 的片段，
  用 `[^，,=]+` 会**在第一个 `=` 处截断**，把尾巴漏成正文。
- 校验重建结果：`vidu_verify.py` 会逐段导出（mention 显示为 `⟦图N⟧`）并检查 **有无 `=@` 残留**。
- 实测：1_1 全 **7 图 + 3 音 = 10** mention 全部落位正确，48 段、正文 3303 字。

## 3. 设置区

| 行 | 控件形态 | 默认 | 操作方式 |
|---|---|---|---|
| 模型 | 下拉 | `Vidu Q4 预览版`（登录后） | 点行内值区展开 → 选 |
| 时长 | ★ **窗口式轮播** | 中段（**点不中会回落 8**） | **先滚到可见再点 + 回读校验**，见下；档位 **1–16** |
| 清晰度 & 编码格式 | 两个下拉 | `1080p` / `H265` | 同上 |
| 宽高比 | 下拉（**向上弹出**） | `16:9` | 选项 `16:9 / 9:16 / 1:1 / 4:3 / 3:4`（本项目要 **9:16**） |
| 数量 | 分段 `1 2 3 4` | `1` | 直接点格 + 回读 |

- 所有行标签在 `x≈137`，值区在 `x≈337–593`。
- ⚠ **时长/数量是刻度条**：用「标签 + 220px」的偏移去点 = **直接选值**，必须按格子文本定位。

### ★★ 时长/数量刻度条：轮播的准确机制（2026-10-07 实测，权威）

DOM 结构（时长行，`y≈359`）：

```text
行容器 x=137 w=456
├─ 标签 span  x=137 w=28        「时长」
└─ 滚动容器 div.hide-scrollbar  x=357 w=215 overflow-x:auto   ← 一次只显示约 8 格
   └─ 16 个格 div(27×36) > label  文本 "1".."16"   等距 27px
      + 一个高亮框 div.absolute.rounded-8.border（宽 27）← 标记当前选中值
```

1. **格子的 `getBoundingClientRect()` 不受 `overflow` 裁剪影响** —— 滚出去的格子照样返回坐标。
   ⇒ 点那个坐标**落在左右箭头上**，**点击静默无效、不报错**。
2. ⛔ **左右箭头点不动**：实测连点 6 次 `Input.dispatchMouseEvent`，
   所有格子的坐标**一模一样**（滚动量 0）。⇒ **别用箭头**。
3. ✅ **正解：直接改滚动容器的 `scrollLeft`**（`JS_ROW_SCROLL_TO`）——
   把目标格中心对齐到可视区中心，再点。
4. **回读选中值只能用高亮框**（`JS_ROW_SELECTED`）：
   `JS_ROW_CURRENT` 对轮播**恒返回 `None`**（它的取值方式只对下拉有效）。
5. ★ **默认值 = 8**：任何一次"点了等于没点"都会让时长悄悄变成 **8s** 提交出去
   （实测 11 组声明 12s 全变 8s，成片 8.04s）。所以**必须回读 + 失败即中止**。
6. **实现侧符号**（`scripts/vidu_ops.py`，排查时按名找）：
   `JS_ROW_CELL` 增**可视区判断**（用 `__scrollStrip()` 找滚动容器，格子未整个落在里面 ⇒ 返回
   `{err:'clipped'}` 让上层去滚动，而不是硬点）；`JS_ROW_SCROLL_TO` 把目标格滚到可视区中心；
   `JS_ROW_SELECTED` 读当前选中值；`set_duration` / `set_cell` / `set_dropdown` 一律**先滚 → 点 → 回读**。

- 「创作」按钮：`(161,634)` `408×48`，文本 `创作` + 积分数字。
  **启用态**：`disabled=false`、`cursor:pointer`、底色 `linear-gradient(270deg,#94D8FF,#E0DDFF)`。
  ⚠ 截图缩略下**容易被看成灰色**，别据此判"禁用"。

## 4. 定位的四个坑（今天全踩过）

1. **整页 `querySelectorAll` 会命中右侧「参考生视频」历史卡片** —— 卡片文案里也有
   「Vidu Q4 预览版」「时长: 16s」⇒ 实测把下拉选项点到了 `(1341,153)`。
   **对策**：查询先缩到**左侧创作面板**（从编辑器往上找第一个 `textContent` 含「宽高比」的祖先），
   候选再限制在行标签右侧 400px 内；找不到才回退全文档。
2. **编辑器内的 mention 徽标"几何溢出"到设置行 y 带** —— 提示词框是内部可滚动容器，
   徽标被裁掉看不见，但 `getBoundingClientRect()` 返回**未裁剪坐标**；
   `图1`(22×20) 面积比 `Vidu Q4 预览版`(101×20) 小 ⇒ 被"取最小"规则抢走。
   **对策**：设置区查询一律**排除 `.tiptap.ProseMirror` 后代**。
3. **`Runtime.evaluate` 的顶层 `const` 留在执行上下文** ⇒ 二次求值报
   `SyntaxError: Identifier '__xxx' has already been declared`。**prelude 必须包进 IIFE。**
4. **时长条第 1–8 格在行标签左边缘之左**（第 1 格 x=142 < 标签右边缘 165）
   ⇒ 用「必须在标签右侧」过滤会全灭。刻度条只能相对 `lab.x` 放宽。

## 5. 组间复位

- **默认不刷新**（用户 2026-10-07 明确要求"就在已开的页面继续操作"）。
- 刷新**唯一目的**是复位：上一组的图若还挂着，新上传的会接着编号成 `图8/图9…`，
  `pnN → 图N` 映射整体错位。
- `--reload auto|always|never`（默认 `auto`）：`auto` 只在表单脏（编辑器 `text_len>5` 或有 mention）时才刷。
- ⚠ 待验证：**「创作」成功后 Vidu 是否自动清空表单**。
  ★ **已实测（2026-10-07）：不清空。** 提交后编辑器仍有全部正文与 mention
  ⇒ `--reload auto` 实际等价于"每组都刷"。**没有任何 DOM 标记能判提交成功**（见 §1.5）。

## 6. 运行环境

- 脚本**必须用系统 Python 3.14** 跑（`websocket-client` 只装在那里；managed 3.13 没装）。
- 浏览器实例一律走 `video_edge.py`（见 `INIT.md`），**独立 `--user-data-dir`**，不要用 junction。
- **CDP 操作必须在沙箱外**（沙箱网络隔离导致连不上 127.0.0.1）。

## 7. 尚未实现 / 待验证（2026-10-11 从 SKILL.md 下沉）

> 属**开发待办 / 机制待验证**，⛔ 不是执行内容 —— 排障或改进脚本时读。

1. **提交时直接落 task id**：`POST /vidu/v1/tasks` 的**响应体**里应含 id，但 `NetWatch`
   目前只记 `url/len`、没存 body ⇒ 现在靠"事后拉台账 ＋ 提示词标记"反查（够用，但多一次往返）。
2. **`failed` 组的原因归类**：`err_code/err_msg` 已在台账里，尚未统计归类
   （首日 `2_1_标准` 第一次"无音色"提交被平台判 `failed`）。
3. **翻页**：`history/me` 每页 100 条，`created_at` 游标翻页**已实现但尚未验证到第 2 页**
   （当前 100 条已覆盖本项目全部任务）。

---

# 主体库 / 提交 API / 闲时（2026-10-13 实测，权威）

> 这一节是**纯 API 化**的基础：主体库管理与任务提交**都不需要点 UI**。
> 全部请求在**页面上下文**里 `fetch(url, {credentials:'include', headers:H})` 发出即可复用登录态。

## 8. ★★ 必需请求头（缺了会被 CORS 预检拒掉）

```js
const H = {'X-Platform':'web', 'X-App-Version':'-', 'Content-Type':'application/json'};
```

- **症状**：只带 `credentials:'include'` 发 `fetch` ⇒ 直接 `TypeError: Failed to fetch`。
  实测预检头 `Access-Control-Request-Headers: x-app-version,x-platform` ⇒ 这两个必须带。
- `X-App-Version: -` 就是字面量减号（页面自己发的就是这个值）。

## 9. ★★ 主体库（material elements）—— base `https://service.vidu.cn`

| 操作 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 列表 | GET | `/vidu/v1/material/elements/personal?pager.pagesz=100` | `pagesz` **上限 100**（200 → 400）；`next_page_token` 翻页 |
| 重名校验 | GET | `/vidu/v1/material/elements/create/check?name=<urlenc>` | → `{"ok":true}` |
| 预处理 | POST | `/vidu/v1/material/elements/pre-process` | 平台 AI 读图 → 生成 `recaption{style,description}`；**已分配 id** |
| 创建 | POST | `/vidu/v1/material/elements` | body 见下 |
| 删除 | DELETE | `/vidu/v1/material/elements/{id}` | → `{}`（200） |
| 标签列表 | GET | `/vidu/v1/material/labels` | → `{labels:[{id,name,creator_id,created_at}]}` |
| 标签创建 | POST | `/vidu/v1/material/labels` | `{"name":"剧名"}` → `{id,name,…}` |
| 标签删除 | DELETE | `/vidu/v1/material/labels/{id}` | → `{}` |

**主体对象**（列表项）：
```jsonc
{"id":"3506772397277986","name":"测试","type":"user","version":"1791873207",   // ★ version 提交时要用
 "components":[{"content":"<content_*.jpeg>","src_img":"<srcimg_*.jpeg>",
                "selected_region":{"top_left":{"x":0,"y":0},"bottom_right":{"x":2048,"y":1136}},
                "type":"main","content_type":"image"}],
 "labels":[{"id":"…","name":"剧名",…}],
 "recaption":{"style":"写实","description":"…"},          // 平台自动生成，无需手写
 "voice":{"id":"","is_my":false}, "modality":"image", "pay_info":{"pay_required":false}}
```

### 9.1 ★★ 图片上传是「三步」，且**要传两份**

```
① POST /tools/v1/files/uploads  {"metadata":{"image-height":"…","image-width":"…"},"scene":"material"}
     → {"id":"350…","put_url":"https://put-aliyun.vidu.cn/tmp/material/…?auth_key=…",
        "headers":{"x-oss-meta-image-height":"…","x-oss-meta-image-width":"…"},"expires_at":"…"}
② PUT <put_url>          头=上一步的 headers，body=**原始图片字节**（不是 base64）
     → 响应头 ETag（去引号后下一步要用）
③ PUT /tools/v1/files/uploads/{id}/finish   {"etag":"…","id":"…"}
```
- ★ **同一张图要传两次**（官方如此）：`components[]` 里 `content`（裁剪图）与 `src_img`（原图）
  用的是**两个不同的 upload id**。实测创建一条主体共发起 2×（POST/PUT/finish）。
- `components[].content` 的值形如 **`ssupload:?id=<upload_id>`** —— 引用上传 id，不是 URL。
- `selected_region` = 原图全幅时填 `{0,0}→{w,h}`（宽高从 `metadata` 那两个值来）。

### 9.1b 音频上传（scene 同样是 `"vidu"`）—— ★ metadata 字段名 = `duration`

```
① POST /tools/v1/files/uploads  {"metadata":{"duration":"5"},"scene":"vidu"}
     → {"id":…, "put_url":…, "headers":{"x-oss-meta-duration":"5"}}
② PUT <put_url>   头 = 上一步 headers ＋ **`Content-Type: audio/wav`**，body = 原始字节
③ PUT /tools/v1/files/uploads/{id}/finish  {"etag":…,"id":…}  → {"uri":"ssupload:?id=…"}
```

- ★★ **为什么字段名这么要命**：init 的 `metadata` 会被服务端转成 PUT 的 `x-oss-meta-*` 请求头，
  而 OSS 的 CORS 只放行**白名单内**的头 ⇒ 名字写错（例如 `audio-duration`）会生成
  `x-oss-meta-audio-duration`，**预检直接失败**，前端只看到 `TypeError: Failed to fetch`
  （不是 4xx，极易误判成"网络不通"）。
- **少了 `duration`** ⇒ 提交时报 `audio duration must be between 1 and 16 seconds`（平台读不出时长）。
- 时长取法：`.wav` 用标准库 `wave` 读 `frames / framerate`，再 `ceil()` 成整数。
- ⚠ 音频上限 **3**；超过平台静默丢弃。
- ★ 对照：**图片**用 `{"image-height","image-width"}`；**主体**用 `scene="material"`。

### 9.2 创建主体的 body（`POST /material/elements`）

```jsonc
{"name":"剧名-角色名","labels":[{"id":"…","name":"剧名"}],   // ★ 标签这么传才生效
 "modality":"image","voice":{"id":"","is_my":false},"type":"user","version":"0",
 "components":[…],"recaption":{…}}                            // recaption 取 pre-process 的返回
```
- ⚠ 实测：**创建响应体里的 `labels` 可能是 `[""]`（回显不准）**，别据此判断失败 ——
  以 `list` 复查为准（实测标签确实打上了）。

### 9.3 工具与纪律

`scripts/vidu_refs.py`（`list｜labels｜create｜import｜delete｜delete-labels`）：
- ★ **每部剧的资产一律打剧名标签**（`--label <剧名>`），这是后续"批量清理"的唯一抓手。
- ★ **「用户常用主体」永不删**：约定带保留标签（默认 `常用`）的主体在清理时跳过
  ⇒ 技能库/常用图先打 `常用` 标签再沉淀。
- ★ **删除默认只预演**，必须显式 `--yes` 才真删；`delete` 必须给 `--ids/--names/--label`
  之一，**拒绝无差别清库**。
- ⚠ 额度：旗舰账号主体库上限 **300**，所以"先建后清"是常态（`import` 前先 `list` 对账）。

## 10. ★★ 提交可以完全不走编辑器：直连 `POST /vidu/v1/tasks`

**实测 200 建单成功**（2026-10-13）。报文形态 = 编辑器提交抓包所见：

```jsonc
POST https://service.vidu.cn/vidu/v1/tasks
{"input":{"prompts":[
    {"type":"text","content":"…镜头词… [@主体名] …"},        // mention 序列化成 [@名称]
    {"type":"material","content":"<content_*.jpeg URL>","name":"主体名",
     "material":{"id":"350…","version":"1791873207"}}],      // ★ 主体走这条
  "editor_mode":"normal","enhance":true},
 "type":"character2video",
 "settings":{"duration":5,"resolution":"1080p","movement_amplitude":"auto",
             "aspect_ratio":"9:16","sample_count":1,"schedule_mode":"normal",
             "codec":"h265","model_version":"3.4","use_trial":false}}
→ 200 {"id":"3506806637505533", …}     // 服务端把 material 转成 infer 输入
```
- **图片 vs 主体**：图片 mention 序列化成 `[@图N]`（并挂 image 条目）；**主体是 `[@主体名]`**
  ＋ `{"type":"material", material:{id,version}}`。
- ★ **为什么重要**：直连一次 POST < 1 秒，而走编辑器要 5–8 秒 UI 操作
  ⇒ **批量/多账号/并发调度必须走直连**（编辑器路径留给单组人工复核）。
- ⚠ 主体 `content` URL 带 `auth_key`（会过期）⇒ **每次提交前重新 `GET /material/elements/personal` 取最新**。
- 只读校验（**不建单**）：`GET /vidu/v1/tasks/credits?type=…&settings.*` —— 排障先打它。

## 11. ★★ 闲时模式 = `settings.schedule_mode: "nopeak"`（**0 积分**）

> ⚠ 2026-10-13 **自纠**：先前误判成"Q4 不支持闲时" —— 其实是**取值没试对**。
> 合法值就两个：**`normal`（消耗积分）** 与 **`nopeak`（闲时，0 积分）**。

`GET /tasks/credits` 实测（`model_version=3.4`、duration=5）：

| schedule_mode | cost_credits | original_cost_credits |
|---|---|---|
| `normal` | **30** | 50 |
| **`nopeak`** | **0** | **0** |

- ★ **实测提交成功且不扣积分**（2026-10-13）：`POST /vidu/v1/tasks` 带
  `"schedule_mode":"nopeak"` ⇒ 200 建单，**提交前后 `credits` 645 → 645 不变**。
- ⛔ **别猜同义词**：`off_peak / offpeak / no_peak / idle / leisure / free / trial /
  low / pass / claw / economy / saver / boost / standard / fast` **全部 400**
  `is not a valid value`。**只有 `normal` 和 `nopeak`**。
- 页面上「闲时创作 用量 147/200」开关 = 这个 `nopeak`；**额度按天刷新**（200/天）。
- ⚠ `claw_pass_quote`（返回里的另一个字段）**是另一回事**：那是 Claw Pass 的**按秒**权益，
  Q4 走 `ineligible_reason: "Q4 only supports credits"` —— **它不影响 `nopeak` 的 0 积分**，
  别把它当成"闲时不支持"的证据（这就是先前误判的由来）。

**并发**：`GET /credit/v1/credits/me` 返回里有 **`concurrency`** 字段
（`credits / credits_free / credits_subscribed / concurrency`）—— 闲时并发额度从这里读。

其他模型档位：`3.0/3.1` 返回 200（cost 20/12，无 `claw_pass_quote`）；`3.2/3.3` 500；`3.4` = Q4。

---

# 出图 / 多账号 / 仅上传模式（2026-10-13 实测，权威）

## 12. ★★ 出图：`text2image` / `reference2image`（与视频**同一个提交端点**）

出图**也是 task** —— 走的还是 `POST /vidu/v1/tasks`，只是 `type` 不同；查询同样走
`history/me`（★ **必须带 `types=` 过滤**，否则混进视频任务）。

**文生图**：
```jsonc
POST /vidu/v1/tasks
{"input":{"prompts":[{"type":"text","content":"…"}]},"type":"text2image",
 "settings":{"duration":0,"resolution":"1080p","aspect_ratio":"16:9","sample_count":1,
             "movement_amplitude":"auto","schedule_mode":"normal",
             "model_version":"3.2_image_2_5_fast","use_trial":true}}
```

**参考图生图**（多一条 image 条目）：
```jsonc
{"input":{"prompts":[
    {"type":"text","content":"…[@图1]"},
    {"type":"image","content":"ssupload:?id=<裁剪图>",
     "src_imgs":["ssupload:?id=<原图>"],          // ★ 注意是 **src_imgs（数组）**
     "selected_region":{"top_left":{"x":0,"y":0},"bottom_right":{"x":W,"y":H}},
     "name":"图1"}]},
 "type":"reference2image",
 "settings":{…, "image_count":1, …}}                 // ★ 没有 duration 概念，恒 0
```

**与 character2video 的字段差异（别搞混）**：
| | 视频 | 出图 |
|---|---|---|
| image 条目 | `src_img`（**单数**，字符串） | **`src_imgs`（数组）** |
| settings | `duration=5/8/…/15` | `duration=0` ＋ **`image_count`** |
| task types | `character2video` | `text2image` / `reference2image` |

**模型（实测三个，credits 均为 6）**：
| 别名 | 显示名 | `model_version` |
|---|---|---|
| `fast` | 全能 Image 2.5 Fast | `3.2_image_2_5_fast` |
| `pro` | 全能 Image 2.5 Pro | `3.2_image_2_5_pro` |
| `base` | 全能 Image（灵感级画质） | `3.2_image_2` |

**查询 / 下载**：
```
GET /vidu/v1/tasks/history/me?pager.pagesz=N&scene_mode=none&types=text2image
creations[].download_uri = …/image.png   ← ★ 无水印
creations[].uri          = …/watermarked.png  ← 带水印
```
★ 上传仍走 §9.1 那三步，`scene` = **`"vidu"`**（图片/音频都是它；主体是 `"material"`）。
★ 出图**实测耗时**：1024/1080p 一张约 **2–3 分钟**（含排队）。

## 13. ⛔⛔ 下载**只认无水印**（`download_uri`），**绝不静默退到 `uri`**

**实测对照（2026-10-13，图片与视频规律一致）**：

| 字段 | 文件名 | 内容 |
|---|---|---|
| `download_uri` / `nomark_uri` | `video.mp4` / `image.png` | ★ **完全干净，无任何水印** |
| `uri` | `watermarked.mp4` / `watermarked.png` | 右下角 **「Vidu AI 生成」** |

- ★ **中间态陷阱**：平台**先置 `success`、稍后才挂无水印文件** ⇒ 此刻 `download_uri` 可能为空，
  而 `uri` 已经有值。**旧代码会在这时退到 `uri`，静默下到带水印版**（已修）。
- 现在的正确行为（`vidu_image.py` / `vidu_tasks.py` 都已改）：
  1. **只认** `download_uri` / `nomark_uri`；
  2. 若只有 `uri` ⇒ 判定为**中间态**，**继续等**（不将就）；
  3. 等超时 ⇒ 报「稍后重跑同一命令即可」，**不下载水印版**；
  4. 只有显式 `--allow-watermark` 才允许退到水印版（且会打告警）。

## 14. ★★ 多账号：一账号 = 一实例 = 一 profile

`vidu_edge.py` 现在按账号隔离，**多账号可同时跑**：

| 账号 | profile | 状态文件 | 默认端口 |
|---|---|---|---|
| 账号1 | `~/.manju-vidu/profiles/账号1` | `~/.manju-vidu/instances/账号1.json` | **9222** |
| 账号2 | `…/profiles/账号2` | `…/instances/账号2.json` | **9223** |
| `账号N` | `…/profiles/账号N` | … | **9221+N** |

```bash
vidu_edge.py up     --account 账号1          # 或 --account-index 1（纯数字，供 .cmd 调用）
vidu_edge.py status --account 账号2
vidu_edge.py accounts                        # 列出本机所有账号实例 + 运行状态
python vidu_shortcuts.py --account-index 1    # 生成桌面「Vidu 账号1 - 启动/关闭.lnk」
```

- ★ **账号名里的数字决定端口与目录**；名字不含数字时**顺延到已用序号的下一号**（防端口撞车）。
- ★ `.cmd` 是**纯 ASCII**，所以快捷方式用 `--account-index N`（中文名不进 `.cmd`）。
- ⚠ 各账号的**登录态、cookie、单例锁全独立** ⇒ 换账号要各自登录一次（登录态持久保留）。
- ⚠ 迁移：旧版单一 profile `~/.manju-vidu/vidu-profile` 直接改名成 `profiles/账号1` 即可（登录态不丢）。

## 15. ★ 仅上传模式（`vidu_api.py raw`）—— **不改用户提示词**

客户带着**自己习惯的提示词**来（格式不一定是我们的），只要"帮上传 + 挂资产 + 提交"：

```bash
vidu_api.py raw --prompt-file 客户提示词.txt \
                --image 图1.png --image 图2.png \
                --audio 音1.wav \
                --duration 5 --ratio 9:16 --mode nopeak [--dry-run]
```

- ★ **文本一个字都不动** —— 与 `run`（会做 `pnN,名=@` → `[@图N]` 转换）的区别就在这。
- 资产**按顺序**挂：`--image` 第 1 个 = `图1`，第 2 个 = `图2`…；音频同理（≤3）。
- ★ 与用户提示词里 `[@图N]` 的对应关系**由用户保证**；我们只在**检测不到引用标记时告警**（仍照提交）。

## ★★ 窗口**不抢焦点**（2026-10-13 根治，取代旧的 `bringToFront`）

**问题**：Chromium 会**节流后台/被遮挡的标签** ⇒ 页面只渲染侧栏（`bodyLen=38`、`fileInput=0`），
页面内的 `fetch` promise 被**冻结**（永远 pending）。
**旧对策**是每次操作前 `Page.bringToFront` —— 但它会把窗口**拽到前台抢焦点**：
用户在用别的软件时窗口反复跳出，**无法接受**。

**现在的做法（从根上取消节流，且完全不抢焦点）**：

1. **启动参数** —— `vidu_edge.py::build_cmdline` 带 4 个开关：
   | 开关 | 作用 |
   |---|---|
   | `--disable-features=CalculateNativeWinOcclusion` | ★ **Windows 专用且最关键**：不再判定"窗口被遮挡" |
   | `--disable-backgrounding-occluded-windows` | 被遮挡的窗口不当后台处理 |
   | `--disable-renderer-backgrounding` | renderer 不做后台降级 |
   | `--disable-background-timer-throttling` | 后台 timer 不节流 |
2. 页面里补 **`Emulation.setFocusEmulationEnabled(true)`** —— 让页面"以为"有焦点
   （对 `document.hasFocus()` / `execCommand` 有用），**同样不抢窗口焦点**。
3. ⛔ **不要再调 `Page.bringToFront`**（全部脚本已移除该调用）。

**实测**（把窗口最小化 4 秒后测）：
- `bodyLen` 保持 **11438**（未降级为 38）
- 页面内 `fetch` 正常返回 **HTTP 200**
⇒ **窗口可以安静待在后台，用户照常用别的软件**。

> 例外：`--headless` 场景本就没有窗口；若哪天又见"只剩侧栏"，先查**启动参数是否被旧实例覆盖**
> （旧进程没有这 4 个开关 ⇒ `vidu_edge.py down` 后再 `up`）。

---

# ★★ 闲时额度门禁（2026-10-13，权威口径）

## 1. 额度**在哪读**（★ 只有这一个可信来源）

```
GET /vidu/v1/tasks/quota
→ {"nopeak_quota":{"used":200,"total":200},"claw_pass_quota":null}
```

- ★★ **`nopeak_quota` 就是页面左下角「闲时创作 ⓘ 用量 used/total」的那个数** ——
  平台自己算的，**唯一权威**。（用户 2026-10-13 指出：页面上明明白白写着，别去猜。）
- ⛔ **不要**用 `/credit/v1/credits/me` 猜额度 —— 那是**积分**，口径完全不同（踩过）。
- ⛔ **不要**用"数今天任务"估算 —— 受 `created_at` 时区（UTC）影响会**严重少算**
  （实测：数出来 33，真实 200）。可用它估"进行中"，**别用它算额度**。

`vidu_api.py quota` 现在直接打印这个数：

```
★ 闲时额度：已用 200 / 200　剩余 0
  ⛔ 今天的闲时额度**已用完** —— 出片会停在门禁处；换账号或等明天 0 点重置。
```

## 2. 门禁行为（提交**之前**必过）

`vidu_api.gate_check(port, need)` → `(ok, info)`

| 结果 | 行为 |
|---|---|
| `left >= need` | 打印「闲时 used/total，剩余 left ⇒ 放行」，继续提交 |
| `left < need` | **停止作业**（退出码 **2**），打印停止面板（见下） |
| 读不到额度 | **不放行也不拦死** —— 打印告警，交给平台提交报错兜底 |

**停止面板长这样**（`vidu_api._quota_stop_msg`）：

```
==================================================================
 ⛔ 闲时额度不足 —— 已停止作业（不硬跑到平台报错，也不空转等待）
==================================================================
 今日闲时：200 / 200（剩余 0）
 本次需要：60

 ⇒ 二选一：
    A. 【换账号】今天继续跑
       python.exe "...\vidu_edge.py" up --account 账号2
       然后重跑本命令 —— 已完成的组会自动跳过
    B. 【等明天】额度 0 点重置
       明早重跑同一条命令即可，接着跑剩下的

 ⚠ 别反复重跑：额度没恢复时，重跑会立刻再次停在这里（不影响已完成部分）。
==================================================================
```

## 3. ★★ 为什么必须「停」而不是「等」

额度当天用完后**不会自己恢复**（**0 点才重置**）。
旧代码在 `slots <= 0` 时**无脑 `sleep` 死等** ⇒ **整夜空转**，用户还以为在跑。
现在**区分两种"投不出去"**（性质完全不同）：

| 情况 | 判据 | 含义 | 处理 |
|---|---|---|---|
| **并发满** | `inflight >= 100` | 任务跑完就释放 | **等**（正常节奏，`sleep` 后重试） |
| **额度用完** | `nopeak_left == 0` | 今天不会恢复 | **停** ＋ 告知 ＋ 支持续跑（退出码 2） |

## 4. 续跑（★ 别用错命令）

| 命令 | 有状态？ | 重跑行为 |
|---|---|---|
| ★ `vidu_queue.py run` | **有**（`_vidu_queue.json` 记 `submitted`） | **自动跳过已完成** ⇒ **首选** |
| `vidu_api.py run` | ⛔ **无** | **会重复提交**！只适合一次性小批量 |

⇒ 换账号 / 等第二天之后，**重跑 `vidu_queue.py run` 同一条命令**即可接着跑。

**换账号**（多账号额度独立）：

```bash
vidu_edge.py up --account 账号2          # 端口 9223，独立 profile 与额度
vidu_queue.py run --jobs <jobs_vidu.json>
```
