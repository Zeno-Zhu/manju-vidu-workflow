# 全流程索引（Phase 0–10）

<!-- (c) manju-vidu-workflow · MJVID:b4c9dafe68 · 溯源标记请勿删除 -->

---

## ★★ 本包（Vidu · 自己人）的适配 —— **先读这一节**

> 本包**不含主工作流的 `main.py` 与 `requirements.txt`** —— 阶段 0–6 由 **AI 直接执行**
> （读规范 → 写文件），**不需要编排脚本**。
> ⇒ 本文件下文凡出现 `python main.py …` 的地方，**在本包里一律不用**，按下面走：

| 本文件写的 | 本包怎么做 |
|---|---|
| `pip install -r requirements.txt` | 只需 **`pip install websocket-client`**（唯一第三方依赖，见 `INIT.md`） |
| `python main.py status` | 直接问 AI「**状态**」；或看项目目录里的产物文件 |
| `python main.py check --point 2 --approve` | **CP-2 是对话里的确认** —— 说一句「批准」即可 |
| `python main.py rerun --type …` | 说「**重做 <哪一步>**」 |
| `python main.py run --step 5.1-7.3` | 按 `phase-05 / 06 / 07` 的阶段卡**逐步做** |
| 选渠道 → `routing-table.md` | ⛔ 本包**单渠道**，无需选；出片见 **`SKILL.md §5`** |
| `manju-asset-casting` 等外部 skill | ⛔ 本包**不带资产库**；用户的资产直接用，缺的才生成（见 `SKILL.md §2`） |
| 环境探活 | `python scripts/vidu_edge.py status`（看端口 ＋ 登录态） |

---

> **本文件现在只是索引。** 每个阶段的**原子细节**（进入条件 / 输入路径 / 分支 / 产出 / 闸门）
> 各自独立成一份阶段卡 —— 别在这里找命令，去对应文件。
>
> Phase 6 / 7 **因渠道而异**：这两份阶段卡只写**主线侧**，渠道取值一律在**渠道差异层**。

---

## 阶段卡一览

| Phase | 阶段卡 | 一句话 |
|---|---|---|
| 0–1 | `phase-00-01-初始化与剧本.md` | 先**探明项目现状**（已有的都不重做）→ 建目录 / 语言检测 / 补时长 / 分集 / 频次分析 |
| 2 | `phase-02-资产盘点.md` | 分批盘点 + 合并去重 → `资产盘点.json`（**CP-1 报告**） |
| 3–4 | `phase-03-04-资产提示词与生图.md` | ★★ **先查库**（库内有走 `manju-asset-casting`）→ 库内没有才生成提示词 → 生图（**CP-2 硬门**） |
| 5 | `phase-05-分镜组.md` | **5.1 结构与时间**（`prompts/5.1-分镜组结构与时间规划.md`）→ **5.2 统一现场**（`5.2-统一现场与连续性规划.md`）→ **5.3 逐Shot摄影**（`5.3-逐Shot联合导演与摄影规划.md`）（**产出不是出片单元**） |
| **6** | `phase-06-提示词.md` | ★ **分叉**：确定性拆组 → 逐组书写 → **零计费自检门** → 提交单 |
| **7** | `phase-07-出片.md` | ★ **分叉**：预检 → 资产就位 → 自检门 → 提交（★ 默认**不等 CP-4**）→ 产物核对 |
| ~~8~~ | — | **已移除**：音色由用户自备 |
| 9–10 | `phase-09-10-分集工程与交付.md` | 素材清单（★ 默认**不做剪映**）→ 交付清单 / 打包 / 摘要（★ 默认**不做 CP-5**） |

**选渠道** → `routing-table.md`（唯一权威）。

---

## 开工前检查（漏了会中途报错）

```bash
cd "."

# 1. 依赖
pip install -r requirements.txt          # python-dotenv / openai / requests

# 2. 密钥（.env）—— 变量名见对应渠道差异层的「协议文档」

# 3. ★ 探活：确认渠道可用（各渠道探活命令不同 → 差异层的「预检 / 网络体检」节）
```

> ★ **外层解释器必须用系统 Python 3.14**（见 `SKILL.md` §七 环境红线）。
> 工作流自带 `Python/pythoncore-3.14-64/python.exe`，但它**没装 requests** ——
> 跑 `h3_batch_all.py` / 出片脚本时解释器选错，整条链都跑不动（且可能无报错）。

---

## 通用操作（不属于某个单一阶段）

### 随时查状态

```bash
python main.py status --project "<项目名>"
```

### 检查点操作

```bash
python main.py check --project "<项目名>"                    # 列出全部检查点状态
python main.py check --project "<项目名>" --point 2          # 看 CP-2 报告
python main.py check --project "<项目名>" --point 2 --approve # 批准，然后重跑 4.1
```

### 单节点重做（改一张图 / 一组片不用全跑）

```bash
python main.py rerun --project "<项目名>" --type asset_image      --id "角色名" --step 4.2
python main.py rerun --project "<项目名>" --type asset_base_image --id "角色名" --step 4.1
python main.py rerun --project "<项目名>" --type video            --id "2_2_标准" --step 7.1-7.3
```

旧产物先移到 `<项目>/_重做历史/<type>_<id>_<时间戳>/`，节点状态重置 `pending`，新产物写回原路径。
**产物清单是唯一事实来源。**
★ **重做的旧产物不是垃圾，别删**（见 `SKILL.md` §5.1）。

### 运行期参数速调（不用改配置文件）

```bash
# 只跑第 1 集，每集最多 2 组（控成本）
python main.py run --project "<项目名>" --step 5.1-7.3 \
  --set batch.video_episode=1 --set batch.video_groups_per_episode=2

# 临时换比例 / 模型
python main.py run --project "<项目名>" --step 4.1-4.4 \
  --set image.asset_image.model="<模型名>"
```

> 具体合法模型名**见对应渠道差异层**（写错模型名时上游常**伪装成**限流或余额不足）。

---

## 关键配置项（`config/default_config.json`）

```jsonc
"image": {
  "primary_platform": "<生图渠道>",
  "primary_model":    "<生图模型>",
  "base_image":  { "model": "<模型>", "size": "<尺寸>" },   // 角色基准图
  "asset_image": { "model": "<模型>", "size": "<尺寸>" }    // 设定版图
},
"video": {
  "platform": "<出片渠道>",
  "model": "<模型>",
  "resolution": "<分辨率>",          // 合法值见渠道差异层
  "aspect_ratio": "<画幅>",
  "seconds": 8,                      // 兜底；正式值由时长档位吸附决定
  "download_on_poll": true,          // 轮询完成立刻下载（防链接过期）
  "submit_concurrency": 3,
  "poll_concurrency": 3,
  "compress_reference_images": false // 参考图不压缩（画质优先）
}
```

**时长口径**：出片时长由各分镜组的镜头时长**累加后吸附**到该渠道的合法档位，
**每个分镜组各用各的**，不全局固定。合法档位与是否支持小数**因渠道而异**（→ 差异层「时长」节）。

**Phase 6 的提示词体系**由 `video.prompt_system` 决定；Phase 7 只提交**当前体系的模板**，
不会把两套都提交（避免重复计费）。

> ★ **配置与代码分离，密钥绝不硬编码**。计费口径以**实测对账**为准，不抄价目表。

---

## 全局排障

| 现象 | 原因 / 处理 |
|---|---|
| 缺少环境变量 | `.env` 没填或没加载 |
| 提示词文件不存在 | `prompts_dir` 解析错误，检查 `config/default_config.json` |
| 脚本报 `No module named 'requests'` | 解释器选错 → 换**系统 Python 3.14** |
| Phase 4 被拦 `CP-2 未批准` | `check --point 2` 看报告，补图后 `--approve` |
| Phase 7 被拦 `CP-4 未批准` | ★ 默认口径下**不该出现**（已不征询）；真遇到就 `--point 4 --approve` |
| 视频参考图为空 | 本集资产图还没**真实生成**（不是 dry-run 占位） |
| 生图返回空文件 | 用错返回字段（有的渠道返 `url`、有的返 `b64_json`）→ 差异层「失败信号」 |
| 视频下不下来 | 链接过期；重跑下载步骤走兜底端点 |
| 成片太短 | 不是档位问题，是**台词太少** → 合并回相邻组 |
| 门禁一直红 | 规格文件过时 → 按提示词重建 |
| 报错像限流 / 余额 | ★ **先怀疑模型标识写错** |

> 各渠道的完整错误码与协议细节 → 该渠道差异层的「失败信号」节 + 项目内协议文档。
