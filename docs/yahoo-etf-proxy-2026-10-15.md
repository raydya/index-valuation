# Yahoo ETF 代理 PE 接入

切换日期：2026-10-15（北京时间）。用户指定纳斯达克100使用 Yahoo QQQ PE、标普500使用 Yahoo SPY PE；此前要求这两个指数停用东方财富、官方点位优先，Yahoo 指数价格作核验。

后续扩展：2026-10-16美国全市场复用同一适配器接入 Yahoo VTI 代理 PE，价格采用 Yahoo 并由 Google Finance 核验，详见 [VTI 接入记录](vti-proxy-2026-10-16.md)。下文两只指数的官方点位规则保持不变。

## 当前来源

| 指数 | 采用的价格 | 价格核验 | PE 代理 | 方法 |
| --- | --- | --- | --- | --- |
| 标普500 | S&P Dow Jones Indices 提供、FRED 分发的 `SP500` | Yahoo `^GSPC` | Yahoo `SPY` | `yahoo_spy_proxy_fred_daily_v1` |
| 纳斯达克100 | Nasdaq 官方 `NDX` | Yahoo `^NDX` | Yahoo `QQQ` | `yahoo_qqq_proxy_nasdaq_daily_v1` |

[FRED 官方来源说明](https://fred.stlouisfed.org/series/SP500)明确标普数据来源及最近10年的日度历史范围。标普官网和盈利文件本次直接请求返回403，所以使用已实测可读的官方数据分发渠道，并保留 `fred` 来源名。[Nasdaq 官方指数页](https://indexes.nasdaq.com/Index/Overview/NDX)。

`price_sources` 是计算允许列表，不只是采集开关；即使数据库还保存东方财富或纳指旧 FRED 记录，也不会参与新方法。`official_price_source` 指定唯一采用点位。Yahoo 缺失或差异超过5%仍采用有效官方值，分别保留 `unverified`、`error` 标记；官方值缺失时不替补。早期官方数据的差异同样保留，不偷偷改用 Yahoo。

这两个指数停止新增采集雪球、Multpl、GuruFocus、东方财富。旧原始记录、`danjuan_index_implied_weekly_v1` 派生数据和原 CSV 保留，新方法导出文件名额外带方法名。其他指数不受此次配置调整影响。

## PE、时间和历史

普通 HTTP 请求可读取 [QQQ](https://finance.yahoo.com/quote/QQQ/) 和 [SPY](https://finance.yahoo.com/quote/SPY/) 页面中的 JSON 数据，使用 `quoteSummary.result[].summaryDetail.trailingPE.raw`。直接 `quoteSummary` 请求本次返回401；适配器读取公开页面内的数据，不调用需要认证的端点、不依赖 Chrome。

同时核验 `price.symbol`、`quoteType=ETF`、USD 以及显式 ETF—指数映射。不得读取 forward PE 或 `topHoldings.equityHoldings.priceToEarnings` 来填补 trailing PE。JSON 数值以 `Decimal` 解析，不把页面两位小数显示值作为原始精度。

这是 ETF 代理，不是指数机构发布的 PE。保存为 `source=yahooetf`、`metric=pe_ttm_proxy`，basis 分别为 `yahoo_etf_spy_trailing_pe_proxy`、`yahoo_etf_qqq_trailing_pe_proxy`。页面显示“ETF 代理 PE”和“代理隐含 EPS”。代理隐含 EPS = 指数点位 / ETF 代理 PE，不代表已经核实的指数实际盈利。

Yahoo 未单独提供 PE 的生效日期。程序将快照关联到 ETF `regularMarketTime` 的纽约日期，检查该交易日已收盘、在请求范围内，并由市场日历过滤；采集时间独立记录。原始 metadata 明确 `pe_effective_date=null`，`date_basis=etf_regular_market_session;pe_effective_date_not_provided`。这是代理快照的关联规则，不声称 PE 与官方收盘价具有已核实的相同估值时点。`marketState=REGULAR` 的盘中快照不入库，盘前盘后价格不参与计算。

仅当前快照可取，不代表能下载历史 PE。日常09:00美股任务自动积累每日快照，重复运行按来源、日期、指标、口径 upsert。回填请求不会把最新 PE 复制到旧日期；缺失日期不补值。均值/分位只使用估值日之前的同代理日度观测，至少需252条，新代理刚启用时为空。旧雪球恐慌阈值保留查阅，比较状态为 `different_basis`，不能套用到新代理或混算阈值。

## 操作和验证

后续仍使用通用增量入口，无需另设调度：

```bash
uv run --locked index-valuation update-index --index sp500
uv run --locked index-valuation update-index --index nasdaq100
# 两者也自动包含在北京时间09:00的美股任务中
uv run --locked index-valuation update-index --market US
```

改动覆盖来源身份与字段校验、时间关联、缺失不填充、官方点位优先、停用旧来源、方法隔离、入库幂等、CSV 来源字段与旧导出保留；网页验证使用 Playwright WebKit。

## 本次运行结果

2026-10-15首次采集后，两只指数均再次执行默认增量更新，状态为 `collected`，日期与方法记录数量不变。PE 快照关联日期均为2026-10-14；该日期不代表 Yahoo 已确认 PE 的生效日。

| 指数 | 官方收盘点位 | Yahoo ETF 代理 PE | 代理隐含 EPS | 当前方法价格覆盖 | 代理 PE 条数 |
| --- | ---: | ---: | ---: | --- | ---: |
| 标普500 | 7718.94 | SPY 24.433956 | 315.9103667044337806 | 2016-10-17—2026-10-14，2512条 | 1 |
| 纳斯达克100 | 29950.82 | QQQ 27.912027 | 1073.0435306615316759 | 1996-06-06—2026-10-14，7638条 | 1 |

最新官方点位与 Yahoo 指数点位的比较均为 `pass`。旧雪球估值和旧方法历史仍在数据库中。新方法仅有一个 PE 日期，固定倍数 band（含8倍）展示为当日参考点，不延长成历史曲线；历史均值、分位为空，恐慌比较为 `different_basis`。

验证结果：218项非数据库测试、17项 PostgreSQL 集成测试通过；Ruff、格式、JavaScript 语法及差异空白检查通过。桌面与390px手机网页完成指数切换、CSV下载、8倍参考点、历史日期缺值以及切回富途标签的检查，无页面脚本错误。验证产物在本机 `data/exports/yahoo-proxy-web-check-2026-10-15.json`、`data/exports/yahoo-proxy-incremental-check-2026-10-15.json`，详细采集日志在 `logs/*-yahoo-proxy*-2026-10-15.json`。
