# 欧乌贸易与实际拨款监测

采集欧盟与乌克兰贸易、重点商品流量和欧盟援乌实际拨款，区分承诺金额与已执行资金。由项目统一爬虫管理器按 `collectors.json` 调用 `eu_ukraine_flow_monitor.py`，数据经统一适配器写入研究快照。

本地自检：

```powershell
python eu_ukraine_flow_monitor.py --self-test
```
