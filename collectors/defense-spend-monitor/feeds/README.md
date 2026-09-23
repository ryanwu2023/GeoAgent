# feeds/ — 离线模式（--from-file）用的本地转存目录

当某个源在当前网络/出口下被风控拦截（403），**不要做绕过**。
正确做法是用浏览器或 RSS 阅读器手动获取内容，转存到本目录，再用离线模式解析。

## 命名约定

| 源类型 | 文件名 | 内容 |
|---|---|---|
| `kind: rss` | `<源id>.xml` | 浏览器打开源的 URL → 另存为 XML；或 RSS 阅读器导出 |
| `kind: federal_register` | `federal-register.json` | `{ "<查询标签>": <documents.json 响应>, ... }` |
| `kind: treasury_mts` | `treasury-mts.json` | `mts_table_5` 的原始 JSON 响应 |
| `kind: usaspending_awards` | `usaspending-munitions.json` | `spending_by_award` 的原始 JSON 响应 |
| `kind: usaspending_category` | `usaspending-psc-category.json` | `spending_by_category/psc` 的原始 JSON 响应 |

`<源id>` 就是 `config.json` 里 `sources[].id`。

## 用法

```bash
python spend_monitor.py --from-file feeds
```

**只对该目录下存在的文件生效**：某个源没有对应文件时，仍然走网络。
所以可以混用 —— 只把被拦的那个源转存下来，其余照常联网。

## 示例：USNI News 被 Cloudflare 拦时

1. 浏览器打开 `https://news.usni.org/feed`
2. 右键 → 网页另存为 → 保存为 `feeds/usni-news.xml`
3. 在 `config.json` 里把 `usni-news` 的 `enabled` 改回 `true`
4. `python spend_monitor.py --from-file feeds`

这样就能在完全合规的前提下拿到正文（RSS 的 `description` 常含全文，不必抓文章页）。

## 注意

- 本目录内容**不参与版本管理**时请自行忽略，避免把大文件提交进仓库
- 离线模式下的「源内最新日期」取自文件内容，因此停更检测依然有效
- 转存文件会随源更新而过时，属于**一次性快照**，需要新数据时请重新转存
