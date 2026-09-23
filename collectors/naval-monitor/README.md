# naval-monitor — 美军海军动态公开来源监测

定期抓取 USNI News、navy.mil、defense.gov **公开发布**的新闻与新闻稿，抽取舰艇、海域、
坐标，产出**可供大模型直接消费的 Markdown 文件**。

---

## 一、能力边界（请先读这一段）

**做：**
- 抓取上述站点公开提供 RSS 的内容。RSS 本身就是为自动化分发而设计的，抓取属于正常使用。
- 从公开文本中抽取：舰名与舷号（CVN / DDG / LHD / LPD / LCS / SSN …）、海域与港口、经纬度。
- 输出结构化 Markdown / JSONL，带坐标，可投图或直接喂给大模型做检索、总结、问答。

**不做：**
- 不抓取 MarineTraffic、VesselFinder 等 AIS 平台的舰船**实时位置**，也不做位置告警。
  原因：这些站点的服务条款禁止自动化抓取；且把军舰实时船位聚合成可检索流，性质上是
  为目标锁定提供支持，超出公开新闻监测的范畴。
- 不做任何反爬/风控绕过（含 Cloudflare 挑战破解）。遇到风控就换网络或改用本地 Feed 模式。

**坐标的准确含义**（重要）：
1. 来源一：**原文显式坐标**，例如文中直接写 `26.57N 56.25E`，原样提取。
2. 来源二：文中提到的**海域/港口**，查内置地名字典得到该地点的**近似中心点**。
   例如文中说"在波斯湾"，则给出 `26.0, 52.0`。

> 这**不是精确舰位**。USNI 的 Fleet Tracker 本身就是"approximate positions"。
> 每行坐标都标注了「关联依据」，标明它是直接同句关联、承前指代，还是多舰多位置需复核。

---

## 二、目录结构

```
naval-monitor/
├── naval_monitor.py        # 主程序（纯标准库，零依赖）
├── config.json             # 数据源、关键词、舷号前缀、排除词组配置
├── run.bat                 # Windows 一键运行
├── samples/                # 合成示例 feed（仅用于预览格式，不是真实数据）
├── feeds/                  # 真实公开数据转存（--from-file 模式读取）
├── state/seen.json         # 去重状态
└── output/
    ├── LATEST.md           # 最近一次日报（滚动窗口快照，见下）
    ├── ALL-records.jsonl   # ★ 跨天累积总档（按 id 去重，永不淘汰）
    ├── index.md            # 归档索引
    └── YYYY-MM-DD/
        ├── naval-digest-YYYY-MM-DD.md    # 主日报（面向大模型）
        ├── coordinates-YYYY-MM-DD.md     # 坐标清单
        ├── records-YYYY-MM-DD.json       # 结构化记录
        └── records-YYYY-MM-DD.jsonl      # 逐行 JSON，便于流式处理
```

### ⚠️ 快照语义（先读这段，否则会误判数据量）

`LATEST.md` 与各日 `naval-digest-*.md` 都是**滚动窗口快照**：收录**回溯窗口内抓到的全部记录**，
而不是「只有当天新增的那几条」。

举个例子（这是实际情况，不是假设）：

| | 窗内记录 | 本次新增 | LATEST.md 条数 |
|---|---|---|---|
| 第 1 天 | 20 | 20 | **20** |
| 第 2 天 | 22（含昨天 20 条） | 2 | **22** ← 不是 2 |
| 第 2 天重跑 | 22 | 0 | **22** ← 不缩水 |
| 第 3 天（旧记录掉出窗口） | 3 | 3 | 3（累积档仍有 25） |

- 头部 `records_new_this_run` = 本次新出现的条数；`records_in_snapshot` = 当前快照总条数。
- **掉出时间窗的记录不会丢** —— 它们在 `ALL-records.jsonl` 里，按 `id` 去重长期累积，
  每条带 `first_seen`（首次出现的日期），适合做跨越数月的长周期分析。
- 若要一份只含新增的日报，看 `output/<日期>/` 头部标注的「本次新增 N 条」。

> 这个坑曾经真实存在：早期版本只把「本次新增」并进当日文件，
> 跨天运行时 `output/<今天>/` 还不存在，LATEST.md 会退化成只有当天新出现的几条（20 条变 2 条）。
> 因为「同一天重跑」时二者等价，只测同日重跑会完全漏掉它。现已修复并写入自检（第 13 项）。

---

## 三、快速开始

无需安装任何依赖，Python 3.8+ 即可。

```bash
cd naval-monitor

python naval_monitor.py --selftest         # 1. 离线自检（14 项），确认解析逻辑正常
python naval_monitor.py --list-sources     # 2. 查看数据源与启用状态
python naval_monitor.py                    # 3. 正式抓取 —— 就这一个命令
```

**大部分情况下不需要任何代理参数。** Python 的 urllib 在 Windows 上会**自动读取
系统代理（注册表）**，`*nix 上读环境变量。脚本会把实际出口打印出来：

```
模式: 系统默认（自动探测到代理: http://127.0.0.1:7897）
```

看到这一行就说明代理已经生效了。本机 2026-09-17 实测：系统代理是
`127.0.0.1:7897`（Clash Verge），不是网上教程常见的 `7890`。
**如果你想确认/排查，别猜端口，直接读注册表：**

```powershell
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' |
  Select-Object ProxyEnable, ProxyServer
```

⚠️ **`--direct` 会刻意绕开你的代理。** 除非你确定代理本身是坏的，否则不要加它 ——
这是最容易踩的坑：明明有可用的代理，却因为加了 `--direct` 而全部连接失败。

| 场景 | 用什么 |
|---|---|
| 本机装了代理（推荐） | **什么都不用加**，直接跑 |
| 想指定其他代理 | `--proxy http://127.0.0.1:<端口>` |
| 确认真要绕过代理直连 | `--direct` |
| 网络受限、完全连不上 | `--from-file feeds`（本地 Feed 模式，见第五节） |

### 常用参数

| 参数 | 说明 |
|---|---|
| `--hours N` | 只看最近 N 小时的条目（默认取 config 的 `lookback_hours`，现为 336 = 14 天） |
| `--source ID` | 只抓指定源，可重复，如 `--source usni-news` |
| `--direct` | **绕开**系统代理直连。多数情况下不该用，见上方警告 |
| `--proxy URL` | 走指定代理；优先级高于 `--direct` |
| `--from-file DIR` | 不联网，解析 `<DIR>/<source_id>.xml` |
| `--out DIR` | 自定义输出目录 |
| `--no-state` | 忽略去重，全部视为新增 |
| `--dry-run` | 只抓取并打印，不写文件 |
| `--quiet` | 减少日志 |
| `--list-sources` | 列出所有源及其 `enabled` 状态 |

Windows 用户可直接双击 `run.bat`，也可传参，例如 `run.bat --proxy http://127.0.0.1:7897`。

---

## 四、抓不到的排查方法（**这一节最值得复用**）

"抓不到"看着是同一个症状，实际可能是**四个完全不同层面的问题**。按顺序逐层排除，
不要一上来就改代码：

| 层 | 先问什么 | 怎么验 |
|---|---|---|
| ① 网络层 | DNS 能解析吗？TCP 能连吗？代理端口对吗？ | `nslookup`；探测本机监听端口；`socket.connect()` |
| ② 风控层 | HTTP 状态码是什么？响应头 `Server` 是谁？ | 打印 `status` + `Server` + `cf-mitigated` |
| ③ 源可用层 | 200 了，但内容是**最新**的吗？ | **比对"源内最新条目的日期"和今天** |
| ④ 解析层 | 字段、正文、日期解析对吗？ | 跑真实数据看抽取结果，别只信自检 |

### 2026-09-17 一次排查中，五个根因各不相同

| # | 症状 | 真实原因 | 修法 |
|---|---|---|---|
| 0 | 全部连接失败 | **用了 `--direct`**，把本来可用的系统代理绕开了 | 去掉 `--direct`，什么都不加直接跑 |
| 1 | 全部连接失败 | 代理端口写成 `7890`，实际是 `7897` | 读注册表确认真实端口（见第三节） |
| 2 | `navy.mil` / `defense.gov` 一律 403 | 只带 `User-Agent`，被 Akamai 拦 | 补齐 `Accept-Language` / `Sec-Fetch-*` / `Upgrade-Insecure-Requests` |
| 3 | `defense.gov` 404 | URL 里 `ContentType=800` 已过期，正确值 `1` | 逐个候选 URL 实测 |
| 4 | **HTTP 200 但一条没入库** | **源站分类 feed 停更**，内容是 3 周前的 | 停更检测 + 改用主站 feed 按标题识别 |
| 5 | 同一 URL 时好时坏 | Akamai 各边缘节点状态不一致（5 次里 4 次 404、1 次 200） | 404 也重试；403/410 才放弃 |

> 第 0 条和第 1 条都属于"出口没选对"，占这次踩坑的一多半 —— 而且它们**看起来和
> 风控拦截一模一样**（都是 403 / 连接失败）。所以排查顺序永远是：**先确认出口，再看状态码。**

> 第 4 条是最阴的坑：**HTTP 200、XML 合法、能解析、有 30 条记录** —— 一切都"正常"，
> 只是内容是三周前的。如果只在代码层面找问题，永远找不到。**必须比对日期。**

### 内建的四个能力（照搬到其他爬虫项目）

1. `--proxy` / `--direct` 一键切换出口，避免把网络问题误判成代码问题；
2. `--from-file` 本地 Feed 模式 —— 完全合规的退路，任何网络环境都能出数据；
3. 每个源一个 `enabled` 开关，失效的源先停用而不是删掉，日后再试；
4. **每源上报「源内最新条目日期」**，超过 `stale_after_days`（默认 14 天）就在日报里
   打 `ok-stale` + ⚠️ 停更提示 —— 让"源站停更"这种问题自己浮出来，不靠人盯。

---

## 五、被拦截时的退路：本地 Feed 模式

1. 用浏览器打开下面任一地址，另存为 XML；
2. 按 `<source_id>.xml` 命名，放进 `feeds/`；
3. 运行 `python naval_monitor.py --from-file feeds`。

```
https://news.usni.org/feed                                     -> usni-news.xml
https://www.navy.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1067&max=50 -> navy-mil-news.xml
https://www.navy.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=2&Site=1067&max=50 -> navy-mil-press.xml
https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=50 -> dod-news.xml
```

也可以让任何 RSS 阅读器（Feedly / Inoreader / 自建 RSSHub）订阅后导出 XML，效果一样。

> ⚠️ `samples/usni-fleet-tracker.xml` 是**合成示例**，不是真实情报；
> `feeds/` 里才是**真实公开数据**（文件头写明了来源链接）。两者不要混淆。

---

## 六、输出长什么样

### 日报结构

1. **一、部署与位置（Fleet Tracker）** — 每周一发布的舰队位置追踪
2. **二、相关新闻与官方通报** — 按优先级和相关性排序，重点源标 🔴
3. **三、坐标汇总** — 舰艇 ↔ 坐标的关联表，带「关联依据」列；母港单列一表
4. **四、抓取状态** — 每个源的状态、命中数、**源内最新条目日期**；停更会告警
5. **五、结构化数据（JSON）** — 完整记录，可直接 `json.loads`

### 坐标准确性：四层防错

坐标表**只收录同一子句内**同时出现舰艇与位置信息的条目。

- **避免张冠李戴**：不会把整篇文章的所有舰艇都挂到某一个坐标上。
- **承前指代**：`...is in the North Arabian Sea; the strike group transited the Strait of
  Hormuz at 26.57N 56.25E.` 后一子句没写舰名，会继承前文主语，标记
  `同句承前指代（子句未重复舰名）`。
- **多舰多位置标记**：一句里出现多舰 + 多个位置时无法仅凭规则确定配对，
  该行标记 `⚠️多舰多位置，配对需复核`。
- **地名误匹配防护**（真实数据逼出来的）：
  - 长名压短名 —— `Newport News` 不会被 `Newport` 抢先匹配（否则 CVN-73 会被投到罗德岛）；
  - 专有名词遮蔽 —— `Newport Manual`（一本海战法手册）里的 `Newport` 不是地名，
    在 `config.json` 的 `place_exclude_phrases` 里声明；
  - 舰名不当地名 —— `San Diego (LPD-22)` 后面紧跟舷号括号时，判定为舰名而非圣地亚哥港；
  - 编制/辖区不投点 —— `5th Fleet` 是司令部不是位置，保留在文字里但不给坐标
    （否则等于把"隶属某舰队"误读成"此刻停在巴林"）。

### 母港 ≠ 当前位置

原文里大量出现 `USS Robert Smalls (CG-62), homeported in Yokosuka, is in the South China Sea.`
这类句子。脚本会自动识别 `homeported / based / forward-deployed` 表述，把地点归入
**单独的「母港 / 驻地」表**，不与部署位置混在一起 —— 母港坐标只用于识别舰艇归属，
绝不能当作部署位置。日报和坐标清单里都有这张独立表。

### 给大模型用

- 整份日报：直接把 `LATEST.md` 塞进上下文。
- 只取结构化字段：读 `records-*.jsonl`，每行一个 JSON 对象，字段包括
  `title / link / published / ships / places / coords / localized / is_tracker / relevance_score`。
- 只取坐标：读 `coordinates-*.md`。

---

## 七、定时抓取

### 方式一：WorkBuddy 自动化（推荐）

新建一条**定时任务**：

- 频率：每天一次（建议 09:00，此时美方前一日发布已落地）
- 任务内容可直接写：

  > 执行 `naval-monitor/naval_monitor.py`（带 `--proxy http://127.0.0.1:7897 --quiet` 参数），
  > 生成当日美军海军动态公开来源监测日报，读取 `naval-monitor/output/LATEST.md`
  > 并汇报本次新增条目数、命中的舰艇与海域；若某数据源状态为 `ok-stale` 或 `failed`，
  > 说明是源站停更还是网络问题。

### 方式二：系统级定时

```bash
# 每天 09:00（Linux / macOS cron）
0 9 * * * cd /path/to/naval-monitor && python naval_monitor.py --proxy http://127.0.0.1:7897 --hours 26 --quiet

# Windows 任务计划程序
程序:    python
参数:    C:\path\to\naval-monitor\naval_monitor.py --proxy http://127.0.0.1:7897 --hours 26 --quiet
```

建议 `--hours` 略大于间隔（每日跑用 26）。但要注意 **Fleet Tracker 是每周一发布**，
若想让日报里稳定带上位置追踪，建议实际用 `--hours 336`；脚本有 `state/seen.json`
去重，重复抓到不会产生重复条目。

### 同日重跑不会丢数据

日报、坐标表、JSONL 都是**按 id 与当天已有记录合并**后落盘的，不是只写「本次新增」。
所以同一天跑第二次、第三次，文件只会越来越大或持平，**不会因为「新增 0 条」被清空**。

日报标题会同时给出两个数字，便于判断：

```
# 美军海军动态监测日报 — 2026-09-17（本次新增 0，当日累计 32）
```

看到 `本次新增 0` 是正常的（当天内容已收全）；只要 `当日累计` 不为 0，文件就是好的。

### 修改了解析逻辑后，怎么让旧记录也用上新逻辑

去重状态（`state/seen.json`）会在**构建记录之前**就跳过已见条目，
所以常规重跑**不会**重新解析旧记录。要回溯重解析，加 `--no-state`：

```bash
python naval_monitor.py --no-state
```

此时已见条目也会重新走一遍解析，按 id 覆盖旧条目落盘 —— 解析器修复即可回溯生效
（例如修掉 `Nimitz-class` 被误判成 `USS Nimitz` 之后，用这条命令刷新历史记录）。
不加 `--no-state` 时，`state/seen.json` 里的条目保持原样，不会被改写。

---

## 八、自定义

**加数据源** —— 编辑 `config.json` 的 `sources` 数组，追加一项并给一个 `id`。
`sources[].tracker_source: true` 可把某源整体标记为"位置追踪"类。

**加海域/港口坐标** —— 编辑 `naval_monitor.py` 的 `GAZETTEER` 字典，
键为小写地名，值为 `(纬度, 经度, 类型)`。类型见 `COORDLESS_PLACE_KINDS`
（`舰队` / `战区` 这类不参与投点）。

**屏蔽误匹配词组** —— `config.json` 的 `place_exclude_phrases`，例如
`["Newport Manual", "Newport News Shipbuilding"]`。

**调相关性** —— `config.json` 的 `relevance_keywords`（命中越多越优先）和
`exclude_keywords`（命中即丢弃）。判定规则：按标题命中 `tracker_title_match`、
或关键词命中 ≥2 个、或抓到舷号，任一满足即保留。

**加舰名映射** —— `naval_monitor.py` 的 `SHIP_NAME_HULL`，用于文本只写舰名没写舷号时补全。

---

## 九、已知限制

- **地名字典是近似中心点**。波斯湾、红海这类大海域的中心点离实际舰位可能数百海里，
  仅适合示意，不能当作位置情报。
- **复合句仍可能交叉配对**。形如 `A 抵达 X，B 前往 Y 并停靠 Z` 的句子，规则难以完全
  切分，这类行会被标记 `⚠️多舰多位置，配对需复核`。已尽量用分号切分子句缓解。
- **Fleet Tracker 每周一发布**，非更新日该栏目为空，属正常现象。
- **`usni-fleet-tracker` 分类 feed 已停更**（2026-09-17 实测停在 8/24，加 `?nocache=1`
  亦然）。已在 config 里 `enabled: false`，Fleet Tracker 改由主站 `usni-news` 的
  feed 按标题识别提供，正文完整（约 1 万字）。若日后恢复，把 `enabled` 改回 `true`。
- **`defense.gov` RSS 端点不稳定**：经 Akamai 各边缘节点状态不一致，实测 5 次里
  4 次 404、1 次 200。脚本已对 404 重试；仍失败属正常波动，不影响其他源。
- **navy.mil / defense.gov 的 RSS 端点可能随站点改版失效**。`--list-sources` 可快速核对；
  若某源长期 `failed`，用 `--from-file` 模式从浏览器导出即可。
- 摘要文本截断至 1200 字符（存库）/ 600 字符（日报展示），全文请点链接。

---

## 十、合规提示

本工具只处理上述站点的**公开发布内容**，输出物会保留原文链接与抓取时间，便于溯源。
请遵守目标站点的 robots.txt 与服务条款（脚本默认 `respect_robots: true`，
并对同一站点设置了请求间隔）。若使用者所在司法辖区对相关信息收集有特别规定，
请自行确认合规性后再使用。
