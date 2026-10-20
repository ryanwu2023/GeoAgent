# 俄乌相关民调（俄罗斯受访者）

这是俄乌主题的结构化民调来源，不是美伊 `opinion-monitor` 的新闻桶，也不是特朗普支持率。

- 一手入口：<https://www.levada.ru/tag/ukraina/feed/>。
- 从 RSS 发现最近两篇俄乌月度报告，回源读取完整调查方法和全国总体结果。
- 两条独立序列：俄罗斯成年受访者倾向开始和平谈判、倾向继续军事行动；同题合并选项占全样本比例，余项包含难以回答。
- 日期取调查结束日，不取报告发布日或采集日；仅保留已核验两期，不插值为每日数据。抓取成功不代表当月已有新调查。
- 调查题目中文译、原文、调查期、样本量、调查方法、来源 URL 和冻结 HTML 均进入快照证据。
- 按 `ukraine / russia_domestic / polling` 显式绑定；不进入美伊页，不代表乌克兰人或所有参战方的意见。
- 战时面对面访谈有非回应、社会期许等偏差风险；支持谈判不等于接受领土让步，不是停火概率、军队支持率或投资信号。

```sh
python collectors/ukraine-opinion-monitor/ukraine_opinion_monitor.py --selftest
python collectors/ukraine-opinion-monitor/ukraine_opinion_monitor.py
```

支持现有 `COLLECTOR_OUTPUT_ROOT` 隔离输出。输出 `geo-v6.json`、`_source_status.json` 和 `raw/`。没有 API Key；保留 HTTPS 证书校验。来源、方法段或总体比例解析失败时非零退出，不用旧值伪装恢复，也不发布空占位指标。

覆盖边界：当前只接入 Levada 俄罗斯成年人两期同题数据，乌克兰、欧盟、美国关于俄乌的其他民调尚未接入；不得把缺少采集误报为不存在相关民调。
