# VTI 基准与官方估值数据复核

研究日期：2026-10-16，北京时间。范围为一手资料、官方公开页面及其公开 JSON；未修改采集配置、应用或数据库，未使用浏览器或绕过登录。本记录补充此前的 [VTI 代理记录](vti-proxy-2026-10-16.md)，旧记录中的覆盖和快照不代表当前可用性。

## 可执行结论

VTI 可以作为独立的 ETF 估值对象，但不能因为其价格容易获取，就认为历史 PE 也已解决。Vanguard 官方确实公开 VTI 持仓 PE 和基准 PE；本次只验证到当前快照和一个有明确日期的季度 PDF，未验证到可稳定回填多年日度 PE/EPS 的免费官方接口。Morningstar 有授权数据服务，但尚未取得 VTI 或这个确切指数的历史 PE 字段、频率和起点样本，不能称为已可接入。

若后续采用 VTI，应建立独立 ETF 方法：价格单位为美元/份，估值为持仓代理 PE，价格除以该 PE 只能称为“代理隐含盈利/份”。不能将 ETF 价格或代理盈利写成指数点位或指数实际 EPS；也不能把今日/月末 PE 配到过去每天的价格。是否有每日或月度历史 PE，仍决定能否形成历史 band 和恐慌分位线。

## 指数身份和连续性

- VTI 当前跟踪 **Morningstar US Total Market Index**。Morningstar 于2026-07-28完成 CRSP Market Indexes 更名，公告明确这次仅变更名称、方法保持不变，并点名 VTI 和 VTSAX。因此2026年更名本身不是一次新基准切换。[Morningstar 官方公告](https://newsroom.morningstar.com/news/news-details/2026/Morningstar-Completes-Rebrand-of-CRSP-Market-Indexes-to-Morningstar-Market-Indexes/default.aspx)
- Vanguard 列示的长期比较基准是拼接序列：2005-04-22及以前为 Dow Jones US Total Stock Market，之后至2013-06-02为 MSCI US Broad Market，此后为 CRSP/现 Morningstar US Total Market。不能将 VTI 自2001年成立以来的所有记录声称为同一当前指数历史。[Vanguard 官方投资概况，第2页](https://institutional.vanguard.com/assets/corp/fund_communications/pdf_publish/us-products/investment-profiles/0970.pdf)
- `CRSPTM1` 是价格回报 PR，`CRSPTMT` 是全收益 TR。Vanguard 的基准 ticker 为后者，但项目的指数点位序列此前选择前者；两者不能混用。[CRSP 官方代码表](https://www.crsp.org/crsp_pdf/crsp-market-update-may-2021/)、[Vanguard 官方产品页](https://advisors.vanguard.com/investments/products/vti/vanguard-total-stock-market-etf)
- 官方 Morningstar 页面对应 portfolio key `716064`；公开 API 确认 PR/USD 的 `secId=F00000P7NM`、`seriesId=IS00001190`。这是 Morningstar 身份标识，不代表富途支持同一代码。[官方指数页](https://indexes.morningstar.com/indexes/details/morningstar-us-total-market-FS00009VTK)、[官方详情 JSON](https://indexes.morningstar.com/api/index-details?version=2&indexId=F00000P7NM)

## Vanguard：可取官方持仓 PE，历史能力仍不足

本次普通 HTTP GET 成功读取以下官方网页内部接口，无需账号：

| 端点 | 2026-10-16实际响应 | 能证明什么 |
| --- | --- | --- |
| [VTI fundamentals](https://advisors.vanguard.com/investments/products/api/funds/0970/analytics/fundamentals) | `peRatio.value="24.0572200000"`，并有 PB、ROE、盈利增长率；PE 对象本身没有日期 | 当前官方快照可以自动解析；未证明 PE 自身生效日或历史访问能力 |
| [VTI portfolio-statistics](https://advisors.vanguard.com/investments/products/api/funds/0970/analytics/portfolio-statistics) | `effectiveDate="2026-09-30"`，持仓数量等 | 这个组合统计对象有月末日期；不能仅因相邻接口就无条件把该日期写给 PE |
| [VTI产品页](https://advisors.vanguard.com/investments/products/vti/vanguard-total-stock-market-etf) | 同样内嵌上述数据；字段定义和最新价格/收益日期分开 | 价格更新至最近交易日不等于 PE 每日更新 |

官方网页的 `peRatioDefinition` 说明，该指标对持仓或基准内股票采用加权调和平均，分母为过去12个月盈利、价格取月末，并排除负盈利公司。因此它是组合估值统计，不是基金会计利润除基金份额；也不能默认与另一个供应商未披露方法的 PE 完全同口径。[Vanguard 官方产品页](https://advisors.vanguard.com/investments/products/vti/vanguard-total-stock-market-etf)

本次可读的官方投资概况 PDF 日期为 **2026-06-30**，第4页同时列 VTI PE **27.0倍**、基准 PE **26.9倍**。这是明确有日期的历史快照，说明官方资料可以支持稀疏历史补录，但该固定 PDF URL 当前只给一份报告，不能据此声称存在连续季度归档或日度历史接口。[Vanguard 官方 PDF，第4页](https://institutional.vanguard.com/assets/corp/fund_communications/pdf_publish/us-products/investment-profiles/0970.pdf)

网页搜索缓存曾呈现2026-08-31的24.7倍，另一个带查询参数的缓存仍是7月。直接 HTTP 当前响应已不同；搜索摘要不能当作最新快照或稳定历史档案。实际接入时须确认 PE 日期字段/页面日期绑定，归档原始响应，不能借用行情时间或相邻对象日期。若最终只能获取月度数据，需另设月度方法、样本门槛和展示，不能把月末值填满交易日。

## Morningstar：官方页面未提供所需历史估值

本次复查 [详情 JSON](https://indexes.morningstar.com/api/index-details?version=2&indexId=F00000P7NM) 返回名称 `Morningstar US Total Market PR USD`，但没有 PE、EPS 或估值历史字段。返回的 `price=7153.503` 与此前项目验证的 TR/PR数值矛盾仍未解决，不能当作已确认的 PR 点位入库。[价格 JSON](https://indexes.morningstar.com/api/index-prices?secId=F00000P7NM) 仍返回 `{"indexPrices":[],"total":0}`；官方详情页亦显示 Performance Data Not Available。这些结果只说明当前公开端点不能满足本项目，并不证明授权产品也没有数据。[官方指数页](https://indexes.morningstar.com/indexes/details/morningstar-us-total-market-FS00009VTK)

Morningstar 的授权路径值得进一步申请样本：其官方说明区分 Direct 工作台与 Licensed Data 数据集成，数据授权取决于字段、用途、交付和分发范围；Direct 席位并不自动代表所有 API 权限。[Morningstar 授权数据说明](https://shareholders.morningstar.com/investor-resources/investor-qa/qa-details/2026/How-does-revenue-per-user-compare-when-licensing-data-as-a-feed-vs--through-Morningstar-Direct-/default.aspx)

官方 Direct Web Services 的公开 Postman 示例确认存在带 `startDate`/`endDate` 的 `POST /direct-web-services/time-series/v1`，需要 Bearer Token。该示例证明时间序列交付机制存在，却没有证明 VTI/上述指数的 PE 就在许可字段中。[官方 API 示例](https://www.postman.com/dynamic-services-morningstar-com/morningstar-direct-web-services/request/14u3ki3/01-submit-request)

如果走授权渠道，先要求对方提供确切证券标识、TTM/forward 区分、亏损处理、调和/算术权重、PE报告日期与发布时点、历史频率/起点、修订规则及10条真实历史样本。只有拿到这些证据才能选用；不能拿 Morningstar 自有的 **US Market Index** PE 图表直接替换 VTI 的 **US Total Market Index**。两者名称相近，而本次未验证为同一基准。

## 本次证据和下一步

原始 HTTP 响应位于忽略提交的 `data/source-research/vti-official-probe-2026-10-16/`：`0.json` 为 Vanguard 页面，`1.json` 为该页面公开 JS，`2.json`/`3.json` 为 Morningstar 详情/价格响应，`vanguard-fundamentals.json` 与 `vanguard-portfolio-statistics.json` 为官方 JSON。每份记录保存 URL、状态码和采集时间。公开 JS 只用于核实页面实际调用的 GET 路径，未执行其中代码。

建议优先验证能取得多年 VTI 历史 PE 的已授权供应商；同时将 Vanguard 保留为带真实日期的口径参考。若最终只有官方月度/季度快照，可以建立稀疏 ETF 估值历史并逐期积累，明确尚不能复用现有日度恐慌线门槛。

## 富途 REST 实测：价格可取，ETF估值接口不支持

使用现有只读授权于2026-10-16北京时间15:56起试取，证券身份返回 `US.VTI`、`stock_type=ETF`。下列结果来自原始 REST 请求，不是被项目现有的指数身份校验提前拦截。

| 核查 | 实际结果 |
| --- | --- |
| `valuation/detail` PE/PB/PS，30年窗口 | 全部HTTP200、`ret_code=0`，历史列表均为空，当前值0，盈利列表为空 |
| `snapshot` | `trust_valid=true`、`equity_valid=false`、`index_valid=false`，PE/TTM PE/EPS均0 |
| 最近已收盘日价格 | 日K返回2026-10-15收盘378.42美元 |
| 有限年度价格试取 | 最早取得2012-01-03，2011及更早抽查年度为空；没有完整下载或核实全部年度 |
| 基准指数映射 | 同日537只美股指数目录没有确认对应基准，既有CRSP候选代码无效；不能据此断言永远不可取得 |

[富途估值详情文档](https://open.futunn.com/zh-cn/api/quote/valuation/detail)明确支持正股和指数，ETF不在支持品类中，合法但无覆盖的证券可返回零值/空结构。这与本次空结果相符，属于接口覆盖限制，不是需要再次扩权的认证错误。[历史K线文档](https://open.futunn.com/zh-cn/api/quote/basic-data/history-kline)虽有 `pe_ratio` 字段，但通用字段存在并不保证ETF值具有组合估值意义。

不能笼统说所有历史K线PE都为零：2016年抽查发现 **117条非零 `pe_ratio`**，2016-02-19至08-04为4.047—4.634倍，其余已抽查年度均为零。该段收盘价/PE几乎恒为24.04，尚无持仓盈利定义或独立同口径数据证实，且专用估值接口没有对应序列。不能把这段孤立、口径未核实的数据当作连续ETF历史PE，更不能据此生成恐慌线。

快照还返回 `pb_ratio=0.301`，但[官方快照文档](https://open.futunn.com/zh-cn/api/quote/realtime/market-snapshot)明确财务字段只有在 `equity_valid=true` 时才有意义，因此也不能采用。快照 `data_date=2026-10-16` 但纽约当日尚未收盘，不把它当成新的已收盘日；上述378.42美元来自10月15日历史K线。

完整机器摘要与原始响应保存在 `data/source-research/vti-futu-rest-2026-10-16/research-summary.json` 及同目录，包含请求时间、证券身份、15个价格请求摘要、3种估值请求、快照、文档副本和先前目录核验路径。全部按研究证据保存，没有入库或变更采集方法。

## 真实历史 PE 的候选渠道

| 渠道 | 已核实能力 | 尚未核实部分 |
| --- | --- | --- |
| [YCharts VTI持仓加权PE](https://ycharts.com/companies/VTI/weighted_average_pe_ratio) | VTI专属指标页有Historical Data/试用入口；定义排除EPS为负的持仓；[Timeseries产品](https://ycharts.com/tables/timeseries/)支持订阅后导出Excel | 尚未取得表格，无法确认VTI起点、原始频率、缺口、盈利期间、加权细节、修订规则或程序接口权限 |
| [Morningstar Direct](https://admainnew.morningstar.com/directhelp/General/Product_Overview.htm) / [Analytics Lab Python SDK](https://docs-analyticslab.morningstar.com/morningstar_data/investment.html) | 有历史持仓/组合统计及按证券和数据点请求时间序列的授权工具 | 没有VTI专属历史PE样本，通用能力不证明这个字段可用；需区分TTM和预测PE、Direct和Indexes的不同聚合政策 |

YCharts直接页面遇到人机验证后未继续操作，专属历史入口和定义由其官方域名搜索索引确认。图表摘要中的少量年份标签不是完整历史起点；产品允许选择日度导出也不代表PE每天独立更新。未注册试用、购买订阅或联系供应商。

Morningstar的[ETF Portfolio定义](https://admainnew.morningstar.com/WebHelp/glossary_definitions/indexes/etfportfolio.htm)包含 **Price/Projected Earnings**，不能看到PE名称就当作TTM。[指数统计FAQ](https://indexes.morningstar.com/api/docs/6849ce94af1830472be6f203)的月度指数历史范围也不能外推为VTI基金的历史覆盖。

## 对接方案与当前边界

如果采用VTI，建议新增独立ETF系列，保留原美国全市场指数记录，不直接替换或拼接价格。价格可用富途REST；在获得有真实日期、同口径的ETF历史PE后，以同日ETF价格/持仓PE计算明确标识的代理隐含盈利，再乘目标倍数生成美元/份估值带。若采用NAV则必须建立独立净值口径，不能与交易价交替补值；历史价格、拆股、分红和收益口径也须明确。

若只有月末PE，就只形成月末估值观测，并为月度采样定义独立方法与最低样本数；不能前填为每日PE，也不能降低既有日度门槛来制造5年恐慌线。当前最直接的下一步是核实授权渠道的一小段真实VTI PE历史，确认后再决定完整回填。当前已确认富途价格可行，但尚没有满足多年PE band和滚动恐慌线要求的可用历史PE序列。
