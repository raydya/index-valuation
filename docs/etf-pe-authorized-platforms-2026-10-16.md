# VTI／美国全市场历史 PE：授权平台核验

研究日期：2026-10-16。范围为公开的一手网页、官方文档和原生报告；没有注册、购买、联系供应商或修改应用、数据库及采集配置。沿用项目已确认的 VTI 基准身份，见 [VTI 官方数据研究](vti-official-data-research-2026-10-16.md)。

## 本次实质进展：Morningstar 已展示目标指数的历史 TTM PE

此前只能确认 Morningstar 有通用历史统计产品；本次找到了**确切目标指数**的官方历史估值图。

Morningstar Indexes 于2026-09-17发布的 [3 Big Risks Index Fund Investors Face Today](https://indexes.morningstar.com/insights/perspective/bltf55ea8d6791cb33f/3-big-risks-index-fund-investors-face-today) 在第一部分介绍 Morningstar US Total Market Index，并直接链接到 [FS00009VTK 指数详情](https://indexes.morningstar.com/indexes/details/morningstar-us-total-market-FS00009VTK?currency=USD&tab=overview&variant=TR)。因此这里不是名称相似的旧 Morningstar US Market Index。

本次下载并目视核对了文章的[官方原图](https://images.contentstack.io/v3/assets/bltabf2a7413d5a8f05/bltd73185c0ae8705bf/6aac34b96ea641897e41fab3/exhibit_1_us_stock_market_more_concentrated.png)：

| 项目 | 图表／正文明确提供的内容 |
| --- | --- |
| 对象 | Morningstar US Total Market Index；2026-08-31有3450只成分 |
| 指标 | P/E Ratio (Trailing 12-Mo) |
| 来源 | Morningstar Direct |
| 图中历史 | 从2011年起；脚注明确指数表现历史自2011年4月开始 |
| 有明确日期的 PE 样本 | 2026-08-31：24.30倍 |
| 较早历史描述 | 正文说明2020—2021年期间曾达到28.0倍；未给精确日，不能按任意日入库 |

这证明目标指数的历史 TTM PE **确实存在于 Morningstar 的研究数据中**。但本次没有取得逐期数字表，也未确认所需商业许可、精确频率和缺口。图像不能代替完整机器可读历史，更不能通过描点制造生产数据。

[美国版文章](https://www.morningstar.com/markets/3-big-risks-index-fund-investors-face-today)及全球站的搜索索引显示“Download CSV”；原图底部也印有该文字。本次美国／全球站普通 HTTP 返回403，可读的 Indexes 与[澳洲转载](https://www.morningstar.com.au/markets/3-big-risks-index-investors-face-today)只提供静态图，没有取得 CSV 的真实链接。未绕过验证。

## 已确认的定期交付形式：IDXSTAT

这次不止找到图表，还找到可直接读取的[官方 IDXSTAT 文件规范，2025年5月版](https://indexes.morningstar.com/api/docs/6840887ebac1aec2573cf168)。它描述如下可供程序接入的正式产品：

- 默认使用 **SFTP** 交付月度 CSV 文件，每个文件包含某指数某有效日的统计数据，通常在月末后的第3个工作日交付。默认分隔符是 `|`，可另约定逗号或制表符。
- 明确列有 `PortfolioName`、`PortfolioId`、`MasterPortfolioId`、`EffectiveDate`、`Currency`、`PublishDateTime`。
- PE 字段为 **`PriceToEarningsTTM`**，定义使用成分公司的标准化盈利收益率及加权调和聚合，不能直接宣称是未经调整的 GAAP 盈利。
- `EarningsGrowthYoY` 的说明明确使用 **价格回报指数点位 / 指数 TTM PE** 推导盈利。该增长字段在这版规范中带星号，表示尚未交付，不能据此声称当前可直接拿到 EPS 字段。

[2025-06-16官方历史扩展公告](https://indexes.morningstar.com/api/docs/6850a71f10c7c2ae1d487ec4)说明 IDXSTAT 一般历史扩至2008年6月，受底层数据覆盖限制。**这不是 VTI／FS00009VTK 从2008年起已有完整 PE 的证明**；目标指数的实际文件范围仍须核验。

官方[方法说明](https://indexes.morningstar.com/api/docs/68408959f809c14abb3e34fb)和[FAQ](https://indexes.morningstar.com/api/docs/6849ce94af1830472be6f203)搜索索引还说明，这套指标在指数组合层计算，同一指数的 PR/TR 等回报版本共用估值比例，月末更新。两份链接本次直接访问返回404，因此这些细节以搜索索引资料作线索，部署前应取得现行原文。2025年5月规范写50%覆盖门槛，而6月公告写至少三分之二，存在版本差异，不应自行选择一个作为当前规则。

**可行的接入方向是取得目标指数的 IDXSTAT 历史和后续文件授权，再做 Python SFTP 适配器。** 不必要求供应商一定提供 REST。还需要确认该产品覆盖新并入的 CRSP／Morningstar Market Indexes，以及图表使用的 Direct 数据与文件口径是否相同；有目标历史图不等于任一产品订阅都自带该字段。

## Morningstar Direct 的 Python／Excel 路径

[官方 Python SDK](https://docs-analyticslab.morningstar.com/morningstar_data/investment.html)支持 `get_investment_data`，按证券标识、数据点、`startDate`／`endDate` 请求时间序列，并返回带 Date 的长表。[数据点设置接口](https://docs-analyticslab.morningstar.com/morningstar_data/lookup.html)返回频率、日期范围等设置，明确有认证和字段权限错误。Direct 工作台、Analytics Lab 和 Licensed Data 的授权范围需分别确认。

本次还发现一个真实的 VTI 旧报告：[Advyzon公开的 Morningstar Direct 原生报告](https://www.advyzon.com/wp-content/aim_doc/Investment%20Summaries%20for%20Underlying%20Funds/AP-INVSUM.pdf)，第2页标注持仓分析日期2022-03-31、Price/Earnings 19.46。但它位于 Value Factors 区域，未标明 TTM；Morningstar 的[ETF组合定义](https://admainnew.morningstar.com/WebHelp/glossary_definitions/indexes/etfportfolio.htm)包含预测 PE。因此这里只能把它作为“Direct 当年已有 VTI PE 输出”的证据，**不能与上述指数 TTM PE 拼接**。

## 其余授权平台的证据强度

| 平台 | 已核实 | 尚未解决 |
| --- | --- | --- |
| [YCharts VTI Weighted Average PE](https://ycharts.com/companies/VTI/weighted_average_pe_ratio) | 有 VTI 专属历史指标入口；定义为持仓 PE 加权，排除负 EPS；[Timeseries](https://ycharts.com/tables/timeseries/)明确支持基金历史数据与 Excel 导出 | 未取得日期—数值样本、原始更新频率或起点；不能从可选择日度导出推断每日有真实 PE。未越过验证或试用门槛 |
| [LSEG Lipper Global Data Feed](https://www.lseg.com/content/dam/data-analytics/en_us/documents/brochures/lseg-data-for-quant-research-brochure.pdf) | 官方列基金组合 PE、EPS growth 等字段，ETF在覆盖范围；月度完整持仓产品可到2006年（where available） | 手册的“全部历史”条款针对价格、分红和表现；不能外推为 VTI PE 自2006年完整。未取得 VTI 专属历史样本 |
| Bloomberg／FactSet | 本次没有拿到可核对的 VTI／CRSPTM1 历史 PE 样本和对应正式字段 | 不能仅凭品牌和通用股票基本面能力声称可用；保留待核实状态 |

YCharts 的[官方 .NET API 仓库](https://github.com/ycharts/YChartsApi)已归档，旧客户端存在不能证明今天给个人订阅开放稳定 API。本次以仍公开说明的 Timeseries 导出能力为准。

## 公开端点实测：取得官方2026年9月末 PE，但尚无历史表

继续读取目标指数公开页面及其声明使用的 JavaScript 后，找到了可无登录读取的身份与文档接口。以下请求均普通 HTTP GET 返回200；没有浏览器、登录、付费访问或绕过验证。

### 确切代码映射

[官方 variants JSON](https://indexes.morningstar.com/api/variants?fundId=FS00009VTK)实际返回：

| 标识 | 目标指数实际响应 |
| --- | --- |
| 标准名称 | Morningstar US Total Market |
| Fund ID | `FS00009VTK` |
| Master Portfolio ID / 页面 Portfolio Key | `716064` |
| Family ID | `IS00001190`，Morningstar Market |
| USD PR | ticker `CRSPTM1`；secId `F00000P7NM`；performanceId `0P0000XNPV` |
| USD TR | ticker `CRSPTMT`；secId `F00000P7NL`；performanceId `0P0000XNPU` |
| 两个 USD 版本的页面起点 | `inceptionDate` 与 `performanceStartDate` 均为 `2011-03-31` |

这里的 `716064` 由返回 JSON 明确标作 `masterPortfolioId` 和 `portfolioKey`；下载持仓 CSV 也使用它作 `PortfolioId`。**IDXSTAT 规范另有字母数字类型的 `PortfolioId`，不能假定其值必然相同**，申请时应让供应商返回实际文件对应关系。

### 可持续检查的官方事实表

[目标相关文档 JSON](https://indexes.morningstar.com/api/index-details/related-documents?indexId=F00000P7NM&portfolioId=716064&indexFamilyId=IS00001190&defaultIndex=&version=2)给出 [TR USD 官方 Factsheet](https://doc.morningstar.com/LatestDoc.aspx?ClientId=morningstarindexes&key=2726f02984615a10&documenttype=124&sourceid=220&language=451&investmentid=F00000P7NL)。它先返回一个 HTML 包装页，再由该页的真实 `iframe.src` 返回 PDF，不需要执行 JavaScript。

2026-10-16实际下载结果：

| 项目 | 事实表记录 |
| --- | --- |
| 身份 | Morningstar US Total Market TR USD，Morningstar ID `F00000P7NL` |
| 数据日期 | **2026-09-30**，PDF每页均标注 |
| 文档发布日期 | 包装页与资源 metadata 为 **2026-10-07**；不作为 PE 有效日 |
| P/E | **23.64倍**，第1页 Value Measures 的 Index 行 |
| 其他同日字段 | P/B 4.73；P/Sales 3.21；P/Cash Flow 17.84；Dividend Yield 1.08% |
| 成分数 | 3,478 |

PDF第3页的真实链接指向[官方 Disclosures and Definitions](https://doc.morningstar.com/LatestDoc.aspx?clientid=morningstarindexes&key=2726f02984615a10&documenttype=124&language=451&investmentid=DISCLSDEFS)，同样可直接读 HTML 包装和 PDF。定义第1—2页明确该 P/E 基于成分股过去12个月盈利，使用持仓加权调和平均，**排除负 PE，超过60倍的成分 PE 按60倍计入**。

这不是单纯的未来 PE，也不是本项目按当前成分重算过去。但它与前述 IDXSTAT 的标准化盈利收益率方法存在明确口径差异；不能把事实表、IDXSTAT、图表中24.30倍的数据点仅按日期直接拼成一条“官方历史 TTM PE”。若采用事实表，应独立保存来源与方法，并确认后续报告延续相同定义。

### 历史文件检查结果

继续沿前端实际使用的[资源文档接口](https://indexes.morningstar.com/api/documents?portfolioId=716064&sortBy=date&sortOrder=-1&page=1&size=20)请求目标组合，`totalDocuments=5`，包含：

- 当期 USD TR、AUD TR 事实表各一份，都指向 `LatestDoc.aspx`；未列旧月份事实表。
- 2026-06-30和2026-09-30两份 Full Holdings CSV。
- 一份2026-09-08的 Reconstitution Summary。

实际下载的2026-09-30持仓 CSV 列只有 `PortfolioId,PortfolioName,EffectiveDate,ConstituentName,IndexWeight`，没有 PE/EPS。`LatestDoc.js` 只调整 iframe 布局，未提供历史日期参数或列表请求。当前公开入口因此确认了**一个有日期、有明确 TTM 口径的真实官方 PE 快照**，没有确认完整历史 PE CSV，也没有确认月度发布承诺或数据服务 SLA。页面稳定可取与获得持续采集／展示许可是不同事项。

公开页面的 Portfolio 卡片由 SAL 组件载入，相关客户端使用令牌与内容权限配置。本次没有把公开页内令牌用于试探其他受限数据服务，也没有穷举历史文档或估值路径。不能据此声称公开 JSON PE 接口已可用。

### 如申请授权，可一次说明的具体需求

官网导航的 [Data 产品页](https://indexes.morningstar.com/research-data-products)主要是 CRSP 研究数据库目录，列举股票、历史指数等产品及 WRDS/Snowflake/MOVEit 等交付渠道；这不能证明这些包自带 IDXSTAT。前端“Contact Us”使用的 `https://go.morningstar.com/idxcontact` 本次返回404，未找到可确认的 IDXSTAT 自助试用入口。

可用的官方询问入口是历史扩展公告明确列出的 **MorningstarIndexesImplementation@morningstar.com**，通用入口为 **indexes@morningstar.com**。本次没有发送邮件或提交表单。申请内容可固定为：

> 申请 Morningstar Equity Index Statistics (IDXSTAT) 的样本／试用及正式交付报价，目标为 Morningstar US Total Market、Fund ID FS00009VTK、Master Portfolio ID 716064，价格回报版本 CRSPTM1 / F00000P7NM / 0P0000XNPV。请确认该原 CRSP 系列是否在产品覆盖内，提供真实 IDXSTAT PortfolioId 对照、PriceToEarningsTTM、EffectiveDate、PublishDateTime、Currency 的最早可用历史与后续月度文件，并给出两个相隔多年的样本区间。需确认与 Direct 图表和公开 factsheet（负 PE 排除、60倍封顶）的口径差异、缺失值及修订政策，并说明个人研究、Python 自动下载、本地数据库及私人网页展示是否可授权。希望同时取得同日 USD PR 收盘点位；接受 SFTP CSV，不限定 REST。

这份申请不会把2011年以来的历史或 IDXSTAT 对该目标的覆盖写成已获确认事实。若供应商仅提供月度数据，应按月度方法验收，不能前填为每天真实 PE。

### 本次原始证据定位

研究原件已归档至被 Git 忽略的 `data/source-research/morningstar-vti-2026-10-16/`；未加入应用数据库或改动采集配置。

| 原件 | 归档文件（相对上述目录） | SHA-256 |
| --- | --- | --- |
| variants JSON | `variants.json` | `086675a6f3adf91821df540600e22e66c060e1a8b521cd48640c8ad1b20a1d5b` |
| 2026-09-30 factsheet PDF | `factsheet-2026-09-30.pdf` | `9911ac37fe5d4c962b1e9c33d28f4971a0d9522c6a7f899867af202238fed1dd` |
| 定义 PDF | `definitions.pdf` | `aa4e3cdafe2ae244d7b575b86ada2f75875d15e6020e13014f0a734839dc4ae3` |
| 目标资源目录 JSON | `documents.json` | `07cff3abebe5e1c1d0bc436969736de903f0cf3c12af4ea858700256e921d90d` |

## 对项目的意义与剩余验收

本次最强方向从“寻找能算 ETF PE 的券商”推进到了**目标指数发行方本身的历史 TTM PE、真实当期事实表与定期文件交付产品**。已找到确切代码映射、2026-09-30官方 PE 23.64倍及其 TTM 定义；另有官方历史图和 IDXSTAT 规范。尚未取得一套口径一致的可入库历史表或账号授权。下一步样本验收应锁定 FS00009VTK／对应 PortfolioId、`PriceToEarningsTTM`、有效日期、发布日期、历史范围和当前交付口径；先验证至少两个相隔多年的区间。

若最后拿到的是月度数据，项目应建立独立月度方法和统计门槛，以同日 PR 点位推导隐含 EPS，不把月末 PE 前填成每日记录。现有 A/H 富途 OpenD、SPX/NDX 富途 REST 及暂停的 ustotal 配置均未改动。
