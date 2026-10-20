# 证券时报地缘冲突动态

固定读取证券时报网的“要闻”“新闻”和 7×24 快讯列表；历史回补时同时使用证券时报站内搜索，筛选美伊冲突、胡塞武装、霍尔木兹海峡、俄乌冲突及相关资产传导内容，再访问证券时报正文页保存中文标题、正文、发布时间和采集时间。

```powershell
python stcn_usiran_monitor.py --selftest
python stcn_usiran_monitor.py
python stcn_usiran_monitor.py --days 180 --max-pages 240
python stcn_usiran_monitor.py --days 180 --search-term 俄乌 --search-start-page 11 --search-delay 3
```

默认回看 30 天，历史回补可显式使用 180 天参数；历史记录累积保存在 `output/geo-v6.json`。来源属于财经媒体报道，证券时报转载的通讯社或官方声明只能证明报道存在，不自动证明声明内容。普通公司、体育和与两个主题无直接关联的市场新闻会被主题门槛排除。
