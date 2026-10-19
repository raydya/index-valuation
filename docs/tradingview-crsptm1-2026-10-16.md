# TradingView 的 CRSPTM1 数据核验

核验日期：2026-10-16，北京时间。范围为 TradingView 官方公开页面、官方文档及现有 MCP 只读接口；未使用浏览器，未修改生产配置、采集代码或数据库。本文补充 [VTI 数据研究](vti-official-data-research-2026-10-16.md)。

## 结论

TradingView 确实收录 **`NASDAQ:CRSPTM1`**，现有接口可以返回指数日线，可作为价格渠道候选。但本次快照、财务和财务历史接口均未取得该指数的 PE/EPS，因此尚不能仅靠这组接口构建历史 PE band。这个结论限定于本次实际可用的接口，不代表 TradingView 所有产品或授权方案都不提供指数估值。

## 证券身份

[TradingView 官方证券页](https://www.tradingview.com/symbols/NASDAQ-CRSPTM1/)当前名称为 **Morningstar US Total Market PR USD**，代码仍为 `CRSPTM1`。公开页面只有概览、社区、季节性及行情栏目，没有展示 PE、EPS 或 Financials 栏目；网页静态正文中的行情值为空，不据此判断接口行情是否可用。

[CRSP 官方数据指南](https://www.crsp.org/research/crspmi-intraday/)第8页确认 `CRSPTM1` 为价格回报 **PR**，CUSIP `12628G101`、ISIN `US12628G1013`；`CRSPTMT` 为全收益 **TR**，二者不可混用。指南所述 CRSP 并入 Morningstar 与平台现用名称一致。

## MCP 实测

| 请求 | 实际结果 | 解释 |
| --- | --- | --- |
| 搜索 `NASDAQ:CRSPTM1` | 成功，`type=index` | 证券代码有效，身份为指数 |
| 最近小窗口日线 | 成功返回日线 | 价格接口可用 |
| OHLCV，`1D`、`summary`、`count=8000`、`format=columns`、`date_to=2026-10-16` | 摘要为3959条，`has_more=false`；`first_t=1295357400`（2011-01-18），`last_t=1791981000`（2026-10-14），最后收盘5494.3698 | 只是供应商覆盖摘要，未下载全部日线，也未独立核验缺口；仍缺最近已收盘的2026-10-15 |
| `snapshot` 批量查询 CRSPTM1 与 AAPL | CRSPTM1 返回 `Quote data not found`；AAPL 返回有效 PE/EPS | 同一接口的股票对照可用，不能将指数结果解释成全接口都失效；日线可用也不保证快照可用 |
| `financials`，TTM PE、basic EPS、diluted EPS | CRSPTM1 均为 `success=false` / 无数据 | 本次未取得当前估值或盈利字段 |
| `financial_history`，FQ/FY，2016-10-15至2026-10-15 | CRSPTM1 均为 `success=false` / 无数据；AAPL 季度 EPS 历史对照成功 | 本次未取得指数历史 PE/EPS，不能据股票历史能力推断指数也有覆盖 |

原始请求与响应证据保存于本机忽略提交的 [研究 JSON](../data/source-research/tradingview-crsptm1-2026-10-16.json)。表内覆盖和错误均为本次观测，不是长期服务承诺。

## 对项目的影响

[TradingView 官方 Pine 文档](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)区分行情请求 `request.security()` 与来自 FactSet 的财务请求 `request.financial()`。某证券或期间没有对应财务字段时，请求会报错，或在 `ignore_invalid_symbol=true` 时返回 `na`；其 PE 示例需要已有 TTM EPS，再计算价格/EPS。因此，有指数图表或日线不等于有指数盈利序列。

后续若采用 TradingView 价格，仍需先取得同一指数、真实日期、明确口径的历史 PE/EPS，核实其频率与覆盖，并独立核验价格。不能把图表技术指标、全收益点位、ETF 持仓 PE 或当前快照当作同口径的历史指数盈利。本次维持美国全市场暂停采集的现有配置，没有恢复旧来源，也没有生成 band 或恐慌线。
