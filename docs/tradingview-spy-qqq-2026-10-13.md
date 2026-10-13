# TradingView 官方 MCP：SPY / QQQ 对应指数估值验证

核验日期：2026-10-13，北京时间约19:53—19:58。行情与估值对照截止美国已收盘交易日2026-10-12。

## 结论

官方 MCP 已授权且可正常取数。实测可以获取标普500、纳斯达克100的指数日线，但两个指数的 PE、PB、PS 均为空，季度和年度财务历史均返回无数据。当前验证范围内，TradingView 可以作为指数价格核验渠道，不能独立提供本项目计算隐含 EPS、历史 PE 分位和估值 band 所需的估值输入。

这不是本轮出现的登录失败或明确权限拒绝：同一账号下，苹果的 PE/PB/PS 快照和季度财务历史均能返回。没有证据表明购买更多交易所行情权限会补齐指数估值字段；也不把本轮缺失推断为未来永久不支持。

## 指数身份

| ETF | 对应指数 | TradingView 指数代码 | 现有项目 key |
| --- | --- | --- | --- |
| SPY | S&P 500，标普500 | `SP:SPX` | `sp500`，与 VOO 共用同一指数 |
| QQQ | Nasdaq-100，纳斯达克100 | `NASDAQ:NDX` | `nasdaq100` |

依据：[SPY 发行人产品页](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy)、[QQQ 发行人产品页](https://www.invesco.com/qqq-etf/en/about.html)。QQQ 的基准不是纳斯达克综合指数。分析对象为指数价格回报点位及该指数的整体估值，ETF 价格和基金持仓估值不能直接代入。

## MCP 实测

| 检查 | `SP:SPX` | `NASDAQ:NDX` |
| --- | --- | --- |
| 证券搜索 | 返回 S&P 500 指数 | 返回 NASDAQ-100 Index |
| 最近5个已收盘交易日日线 | 成功 | 成功 |
| `get_symbol_data_batch` 的 PE/PB/PS | 全部 `null` | 全部 `null` |
| `get_financials` 的 PE/PB/PS | 全部 `null` | 全部 `null` |
| `get_financial_history`，季度 | `no fundamental history` | `no fundamental history` |
| `get_financial_history`，年度 | `no fundamental history` | `no fundamental history` |

估值字段先用 `get_screener_columns` 发现，再请求 `price_earnings_ttm`、`price_book_fq`、`price_sales_current`；目录将这些字段归于股票市场。历史财务窗口为2016-10-12—2026-10-12，季度和年度分别测试，不能将季度财务工具名称理解为“每日指数 PE 历史”。

额外检查：

- 苹果 `NASDAQ:AAPL` 的相同估值字段非空，财务历史返回6个季度标签，仅用来验证接口和账户路径正常，不据此评价这些公司财务数值的准确性。
- ETF `AMEX:SPY`、`NASDAQ:QQQ` 在同一批查询中的 PE/PB/PS 也为空。
- `TVC:SPX` 可以被搜索到，但本次批量快照返回 `Quote data not found`。这项单独记为报价缺失，不冒充权限错误。
- 以 `S&P 500 P/E`、`Nasdaq 100 P/E`、`SP500_PE_RATIO` 搜索，没有获得身份可确认的专门指数估值序列。这是限定查询范围内的结果，不是对整个 TradingView 数据库的穷尽证明。

[官方 MCP 文档](https://www.tradingview.com/mcp/docs)将当前基本面快照和季度/年度财务历史分开描述。本次18次 MCP 调用的参数、结果和失败响应保存在[完整原始响应](../data/raw/tradingview_mcp_probe/864a4d134e7f5496d30b1bd726c300221fc603373287853480cc1a28a1b72be0.json)。

## 指数价格交叉核验

比较2026-10-06、10-07、10-08、10-09、10-12五个交易日。TradingView 时间戳按纽约时区解析，使用日线 `close`，与项目现有 Yahoo 日线适配器的 `^GSPC`、`^NDX` 对照，并核对美国交易日历。

| 指数 | 重合交易日 | 最大相对差异 | 结果 |
| --- | --- | --- | --- |
| 标普500 | 5 | 0.000003002% | 全部低于1%阈值 |
| 纳斯达克100 | 5 | 0.000687587% | 全部低于1%阈值 |

相对差异使用 `Decimal`，以 TradingView 点位为分母。这是两个分发渠道的同日价格一致性核验；指数仍由同一指数机构编制，不能把它说成对盈利口径的独立验证，也不能将5日样本结论推广为全历史已经核验。

## 现有 PE 来源的本轮复核

通过现有雪球基金适配器重新请求2016-10-12—2026-10-12的数据，并按项目美国交易日历过滤：

| 指数 | 解析出的非零 PE 日期 | 其中交易日观测 | 非交易日观测 | 交易日观测范围 |
| --- | --- | --- | --- | --- |
| 标普500 | 516 | 470 | 46 | 2016-10-12—2026-10-12 |
| 纳斯达克100 | 514 | 469 | 45 | 2016-10-12—2026-10-12 |

这些数量是原始估值观测，不能等同于通过财务口径核验的 PE，更不是连续十年每日 PE。长历史为稀疏周采样，另有当前快照；非交易日数据不移动到相邻交易日，缺失日期不前向填充。现有 `danjuan_index_implied_weekly_v1` 方法以已结束自然周为统计单位。

2026-10-12的同日公开参考值亦重新读取：

| 指数 | 雪球基金主源 | 参考来源及 PE | 相对主源差异 | 结论 |
| --- | --- | --- | --- | --- |
| 标普500 | 25.3298 | Multpl 26.64 | 5.172564% | 重大差异 |
| 标普500 | 25.3298 | State Street 指数特征 25.13 | 0.788794% | 数值接近，完整口径仍未核实 |
| 纳斯达克100 | 30.7803 | GuruFocus 28.93 | 6.011312% | 重大差异 |

参考来源：[Multpl](https://www.multpl.com/s-p-500-pe-ratio)、[SPY 发行人页面的 Index Characteristics](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy)、[GuruFocus Nasdaq-100 PE](https://www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio)。这些网页快照不等于已建立稳定的 API 采集。

State Street 在指数特征栏目公布的25.13与基金栏目预测 FY1 PE 20.79是不同字段，不能混用；该指数 PE 目前只确认了栏目和日期，尚未完整核实盈利期、亏损处理和计算方法。它更接近雪球，也不能消除同日 Multpl 的重大差异。项目现有规则下，两指数2026-10-12的主源 PE 仍不具备派生 EPS/band 的资格；保留原值和对照，不用新的参考来源掩盖冲突。

## 对项目的应用判断

- SPY 对应现有 `sp500`，QQQ 对应现有 `nasdaq100`，可以复用已有直接指数估值流程。历史参考 band 按现有周频方法和质量标记解读。
- TradingView 本轮证实可用于价格核验。缺少指数 PE/EPS 时，不能仅凭价格算出盈利和估值分位；当前证据不足以将它设为两个指数的估值主源。
- 获取完整日度历史估值，还需验证专门的指数估值接口，逐项确认两指数覆盖、TTM/预期盈利口径、亏损处理、日期与历史密度。同一统计序列不拼接不同口径，不用当前成分股反推过去指数盈利。

机器可读核验结果及各来源归档路径见[验证报告 JSON](../data/exports/tradingview-spy-qqq-verification-2026-10-13.json)。网页参考值另存[带日期和 URL 的证据记录](../data/raw/public_valuation_reference/25010e2580fec5de9e08681b0f81fc1175b743bc1d80bbeb3c60ae468854d35a.json)。

后续对 ETF 与指数的行情、技术指标、基金资料、资金流、分红和成分筛选进行了扩展实测，见[TradingView 数据能力清单](tradingview-etf-index-data-2026-10-13.md)。
