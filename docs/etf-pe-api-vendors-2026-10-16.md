# VTI PE：专门 ETF 数据 API 核验（2026-10-16）

本记录只研究数据来源，没有修改配置、适配器、数据库或调度。目标是 VTI 的持仓估值，不能把基金净投资收益、资本利得或股票公司 EPS 当成 ETF 持仓 EPS；历史 PE 必须有真实估值日期。

## 结论

本组供应商中，**Business Quant 已在官方 API 文档中给出 VTI 的持仓 PE 数值示例；ETF Holdings API 提供带历史日期的组合报告并设计了 PE 聚合字段**。两者是值得继续授权试取的具体 API 路径，但本次尚未取得 VTI 的多日期历史 PE 样本，不能声称已经解决历史 band 数据。

| 来源 | 已确认能力 | 历史 PE 是否已验证 | 本次限制 |
| --- | --- | --- | --- |
| Business Quant | VTI 示例 `lookthrough_pe = 24.02`，持仓调和加权，剔除亏损股 | 否；对比接口是快照，历史指标接口未证明覆盖该基金字段 | 缺 API key；文档无独立 PE 生效日期，示例存在其他字段疑点 |
| ETF Holdings API | 历史组合报告具有 PE 聚合、覆盖率及估值日期字段 | 否；历史持仓日期不保证其估值日期也历史化 | 需 API key 和计费权限；没有 VTI 专属历史样本 |
| EODHD | 官方 demo 可直接查询 VTI，提供 ETF 估值字段结构 | 否 | 实测估值为空；字段是预期 PE，非 TTM |
| Intrinio | ETF 当前分析及历史收益/NAV 统计 | 否 | 已核对的 ETF 历史统计 schema 没有 PE/EPS |
| FMP | ETF 信息、持仓、历史披露 | 否 | 未找到 VTI 持仓 PE 历史样本或明确端点 |
| QuoteMedia | 基金数据及历史公司估值接口 | 否 | 未证明公司历史估值接口可返回 VTI 持仓 PE |
| ETF Global | ETF 数据、历史产品及 P/E 相关评分 | 否 | 已找到的是 P/E 评分，未证明为原始 PE 倍数 |
| SecuritiesDB | ETF X-Ray 文档列持仓加权 PE | 否 | VTI 实际调用 HTTP 403；文档没有历史日期查询 |

## Business Quant：VTI 当前持仓 PE 有明确示例

[Fund Comparison 文档](https://businessquant.com/docs/api/funds-compare)给出的 VTI/VTSAX 示例中，VTI 身份为 ETF Shares，`series_id = S000002848`，`lookthrough_pe = 24.02`。同一对象的 `lastreportperiod` 是 `2026-07-31`，`price_asof` 是 `2026-09-10`；这些是持仓和价格日期，不能推定为 PE 日期。文档将 PE 定义为持仓股票的调和加权 PE，排除亏损公司。

实际调用（不带密钥）如下，返回 HTTP 401、`API key is missing.`：

```text
GET https://data.businessquant.com/funds/compare?tickers=VTI,VTSAX
```

正式请求需附 `api_key`。该接口只列 `tickers`、`include_metrics` 等参数，没有历史日期。文档示例还将 VTI 的 `benchmark_ticker` 写为 `I:SP500`，不能采用它覆盖项目已核实的 VTI 基准；独立 PE 日期、TTM/forward 口径也须先验证。

[Historical Metrics 文档](https://businessquant.com/docs/api/historic)有 `price-to-earnings` 和起止日期，但它是股票基本面指标接口，未证明对应基金 `lookthrough_pe`。[Fund Reporting Periods](https://businessquant.com/docs/api/funds-periods)和[Portfolio Exposure](https://businessquant.com/docs/api/funds-exposure)支持历史持仓/配置，不等于历史估值。官方[价格页](https://businessquant.com/pricing)与部分端点的“免费”宣传需要按实际账户核对，不能据此承诺所有基金历史功能免费。

## ETF Holdings API：有历史报告入口，须核验估值时间

[官方 API 文档](https://etf-holdings.com/docs)明确列出：

```text
GET /api/v1/historical/coverage?ticker=VTI
GET /api/v1/reports/historical?ticker=VTI&date=2024-06-30
Authorization: Bearer <API key>
```

报告结构包含 `portfolio.analysis.valuations.priceToEarnings.a.value`，采用有效正值的加权调和比率，另有 `coverage`、`count` 和 `asOf`。文档明确提醒，持仓上的估值 `asOf` 可以不同于持仓快照日期；聚合 `asOf` 是所覆盖持仓估值日期的最新值。因此，必须检查历史请求是否使用当时 PE，排除“旧持仓乘当前估值”的情况。

本次无密钥请求 coverage 返回 HTTP 403（`error code: 1010`），未绕过限制、未调用付费报告。文档要求所有 v1 请求使用 API key。其[官网价格说明](https://etf-holdings.com/)为按返回持仓计费，每十万条 1 美元，最低预付 10 美元；这是报价核查，不代表已购买或推荐购买。VTI 可用年份、TTM/forward、真实历史估值覆盖尚待有权限的样本验证。

## EODHD：公开 demo 可调用，但本次 VTI 估值为空

[官方 Fundamentals 文档](https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds)明确允许 VTI 使用 `demo` token。2026-10-16 直接 HTTP 请求成功：

```text
GET https://eodhd.com/api/fundamentals/VTI.US?api_token=demo&fmt=json&filter=General,ETF_Data::Valuations_Growth
```

返回 `General.Code = VTI`、`General.Type = ETF`、`UpdatedAt = 2026-10-15`；`Valuations_Rates_Portfolio` 下的 `Price/Prospective Earnings`、PB、PS、PCF 均为 `null`。[ETF 字段字典](https://eodhd.com/financial-academy/financial-faq/fundamentals-glossary-etf)定义该 PE 为预期盈利比率，由持仓计算。`UpdatedAt` 是整个基本面记录的更新时间，不是 PE 生效日。

Fundamentals 的 `historical=1`、`from`、`to` 参数明确用于指数成分历史，不应套用为 ETF PE 历史接口。股票多年财报覆盖也不能推及 VTI。

## Intrinio、FMP、QuoteMedia 和 ETF Global

- **Intrinio**：[ETF Analytics](https://docs.intrinio.com/documentation/web_api/get_etf_analytics_v2)是最新行情分析；[ETF Historical Stats](https://docs.intrinio.com/documentation/web_api/get_etf_historical_stats_v2)虽然支持日期范围，完整输出字段是 NAV、收益率、波动等，没有 PE/EPS。通用股票历史 `pricetoearnings` 示例不能证明 ETF 覆盖。
- **FMP**：[官方稳定版接口目录](https://site.financialmodelingprep.com/developer/docs)确认 ETF info、holdings、fund disclosures；[FAQ](https://site.financialmodelingprep.com/faqs?code=data)确认部分历史持仓/AUM 能力。本次未发现 VTI 历史持仓 PE 的官方样本，不能用 AAPL ratios 或持仓历史代替。
- **QuoteMedia**：[官方接口产品说明](https://qrm-version.wdev.quotemedia.com/pdfs/QuoteMediaXMLJSON.pdf)同时列公司历史 Key Ratios 和 Fund Fundamentals，但未证明前者覆盖 VTI 持仓估值。[Cloud API 产品页](https://catalog.quotemedia.com/quotemedia-on-demand)说明可用 JSON/XML/CSV 获取基金数据，仍需具体字段与授权样本。PDF 全文抓取受体积限制，仅使用搜索可见官方摘要，未宣称完整审阅。
- **ETF Global**：[官方数据字典](https://media.etfg.com/files/AWS%20Junto/1.1.21%20-%20ETF%20Global%20Data%20Dictionary%20-%202021.pdf)公开索引含 `quant_fundamental_pe`，定义为成分 P/E 评分。评分不能作为 PE 倍数计算 EPS。PDF 正文获取超时，尚无 VTI 原始历史 PE 样本。
- **SecuritiesDB**：[ETF X-Ray 官方说明](https://www.securitiesdb.com/developers/etf-xray-api)列 VTI 并提供 `valuation.weighted_pe` 路径，未列历史查询参数。直接读取 `https://securitiesdb.com/api/v1/etfs/VTI/xray` 返回 HTTP 403，未绕过。该网站自己说明是公共及第三方数据的聚合，不能等同发行人官方 PE。

## 下一轮有权限时的验收条件

优先对具有具体字段的 Business Quant 或 ETF Holdings API 做小样本核验，不要先购买多年数据或修改生产源。需拿到 VTI 至少两个相隔一年的日期，连同 PE 的真实生效日期、TTM/forward、持仓权重日期、亏损处理、覆盖率和原始响应；与同日 Vanguard 披露作对照。历史报告若使用当前估值或字段为空，即不能用于历史 band。所有月度或季度数据维持其真实采样，不扩展为伪造日度历史。
