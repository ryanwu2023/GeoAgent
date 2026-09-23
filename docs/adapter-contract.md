# 爬虫接入协议 geo.v6/1

每个子项目可保持自己的采集代码。平台只读取 `<CRAWLER_ROOT>/<project>/output/geo-v6.json`。写入文件时建议先写临时文件再原子替换，避免导入半成品。原始文件被归档，失败不会发布不完整的新快照。

## 记录

`records` 中每条需要 `id`、`title`、`summary`、`link`；`topics` 为 `usiran` 或 `ukraine` 数组，`dimensions` 为主题到维度的映射。

美伊维度：`us_readiness`、`iran_readiness`、`financial`、`us_opinion`、`israel`、`regional`、`iran_domestic`、`diplomacy`。

俄乌维度：`frontline`、`aid`、`russia_domestic`、`eu_domestic`、`diplomacy`。

保留 `source_id`、`source_name`、`publisher`、`tier`、`party`、`published`、`fetched_at`、`first_seen`、`event_at`、`verification`。未知时间不编造。来源等级与核验状态独立。

`original_url` 可显式指向转载的原始出处；没有证据时不要填写。相同来源URL归并，原始文件仍完整保留。

## 地点

`points` 可选。每点需要 `lat`、`lon`、`label`、`precision`、`evidence_text`。`precision` 只能为 `explicit`（来源明确位置）、`area`（区域近似）、`mention`（文章提及）。`role` 解释地点与事件关系。即使明确坐标也不等于实时舰位。平台不会生成航迹。

## 指标

`observations` 每项需要 `id`、`name`、`unit`、`frequency`、`scope`、`method`、`history`、`source_ids`。可附 `topic`、`dimension`、`boundary`。

`history` 是 `{"date":"YYYY-MM-DD","value":数字或null}` 数组。缺失保留 null，不能填零。`source_ids` 指向本文件中的记录ID。每项只容纳同一单位、频率和口径；若发生口径变化，使用新的指标ID。时间序列按日期排序，比较前一观测，前值为零不计算百分比。

定性指标通过事件记录、原文、声明主体和维度入库，不需要伪造数值。后续人工复核或语义分析须保留引用。

## 中文与综合事件地图

`title_zh`、`summary_zh` 可提供爬虫已有的中文标题、摘要；英文原文仍保存在 `title`、`summary`。没有中文字段时，由独立本地翻译缓存生成中文阅读层，不改变原始快照。原始来源链接不翻译、不替换。

`event_category` 可为 `military`（军事行动）、`diplomacy`（外交谈判）、`policy`（制裁与政策）、`energy`（能源与运输）、`support`（军费与援助）、`domestic`（政治与社会）。地图不再局限于海军爬虫。

`situation_signal` 可为 `escalation`、`sustain`、`easing`、`unknown`，分别表示升级相关、持续相关、缓和相关和待研判。填写时必须提供 `signal_basis`。这些是单条报道的观察线索，不是整体局势结论，也不是事件概率。未提供时用标题关键词作初步归类；没有匹配则待研判，不能默认持续或稳定。

未提供坐标的主题报道，平台可按明确出现的地名给出地区参考点，标为“报道关联地区”。此类参考点不表示精确事件现场；无匹配地名则只进入台账。多个类别在同一地区聚合，并保留全部相关新闻。

## 模型研究接口

`POST /api/ask` 输入 `snapshot_id`、`question`、`topic`、可选 `dimension/start/end/use_model`。返回模式、程序事实、模型推论、引用、缺口及步骤。所有查询使用冻结快照。

`GET /api/workspace` 提供统一指标、证据、情景及资产视图。`POST /api/briefs` 生成整个快照的双主题简报，不受当前页面的报道日期筛选影响。原始归档通过证据详情下载。
