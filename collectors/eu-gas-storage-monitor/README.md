# 欧洲天然气库存监测

采集欧洲天然气库存、库存率、注气量和取气量，作为俄乌冲突经能源供给传导至通胀和欧洲资产的量化证据。由项目统一爬虫管理器按 `collectors.json` 调用 `eu_gas_storage_monitor.py`；输出进入本目录的 `output`，随后由快照导入流程统一归档。

本地自检：

```powershell
python eu_gas_storage_monitor.py --self-test
```
