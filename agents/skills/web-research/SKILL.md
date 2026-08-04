---
name: web-research
description: 免 API key 的联网搜索与网页研究：搜索网页、抓取正文并整理成带出处的简报。当用户需要实时信息、最新新闻、查找网页内容或核实事实时使用。
tool-names: [search, research]
metadata:
  auto_load: "true"
---

# 联网研究

当用户需要实时或外部信息时，执行：搜索 → 筛选可信来源 → 抓取正文 → 总结。

- 只引用结果中带出处的信息；无法验证的说法明确标注"未能核实"。
- 优先使用 `research` 一次完成搜索与整理；需要多条独立证据时用 `search` 后逐条 `fetch`。
- 总结时区分"来自搜索结果的事实"与"你的推断"。
