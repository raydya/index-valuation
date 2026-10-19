# moomoo OpenAPI 与 ETF 历史估值核验

核验日期：2026-10-16。目标为 VTI、VOO、QQQ 的历史 PE/EPS。先查询官方文档并通过已有富途 OpenD 做对照，随后在用户启动并登录 Moomoo OpenD 后，完成独立 moomoo 行情连接的实际试取。结果是 ETF 价格可取，但三只 ETF 的专用历史 PE、快照 PE/EPS 和财务报表均没有有效结果；VTI 历史 K 线另有一段口径未核实的非零 PE，仍不能用于本项目 band。没有修改生产来源、配置或数据库。

## 官方接口

moomoo 同时提供 OpenD + SDK 和无需 OpenD 的 REST API。[REST 入门文档](https://open.moomoo.com/api/overview/getting-started)列出的 HTTP 基址为 `https://webapi.moomoo.com`，支持 OAuth 2.1 / PKCE 与签名 AppKey。

| 路径 | 官方说明 | 对 ETF 的结论 |
| --- | --- | --- |
| REST `GET /api/v1.0/quote/{symbol}/history-kline` | [历史 K 线](https://open.moomoo.com/api/quote/basic-data/history-kline)支持 ETF，响应含通用 `pe_ratio` 字段 | 支持 ETF 价格，不等于 ETF PE 字段有效 |
| REST `GET /api/v1.0/quote/{symbol}/valuation/detail` | [估值详情](https://open.moomoo.com/api/quote/valuation/detail)明确排除 ETF；未覆盖证券可返回零值或空结构 | 不能据此承诺取得三只 ETF 的历史 PE |
| OpenD `get_valuation_detail(code, valuation_type=None, interval_type=None)` | [v10.11 文档](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-valuation-detail.html)限制段写支持普通股票、基金、指数；返回历史估值列表 | 与 REST 范围表述不同，但文档没有 VTI、VOO、QQQ 非空样本，须实测 |
| OpenD `get_market_snapshot(code_list)` | [快照文档](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html)要求 `equity_valid=true` 时正股 PE/EPS 字段才有效，基金另有 `trust_valid` 和基金字段 | 通用响应含 PE/EPS 列不代表 ETF 有有效值 |
| OpenD `get_financials_statements(code, statement_type=None, financial_type=None, currency_code=None, next_key=None, num=None)` | [财务报表文档](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-financials-statements.html)写明支持股票和基金 | 即使返回基金自身利润，也须区分基金投资损益与持仓公司盈利 |

OpenD 参数可明确选择 PE 与 5 年等窗口，最长可请求 30 年；请求窗口不代表实际覆盖长度。每 30 秒最多 30 次请求。官方示例是普通股票，不能证明 ETF 持仓盈利口径。

## 已有富途 OpenD 的同名接口对照

使用项目已安装的 `futu-api 10.11.7108`，只创建 `OpenQuoteContext`，对四个证券分别请求 `valuation_type=ValuationType.PE`、`interval_type=ValuationIntervalType.YEAR_5`。未请求交易、账户资产或持仓。

| 证券 | 返回码 | 历史点数 | 实际响应 |
| --- | --- | --- | --- |
| US.AAPL | 0 | 1,254 | 2021-10-18—2026-10-15，历史 PE 全部非零 |
| US.VTI | 0 | 0 | `trend.historical_items=[]`，没有 `current_value` |
| US.VOO | 0 | 0 | `trend.historical_items=[]`，没有 `current_value` |
| US.QQQ | 0 | 0 | `trend.historical_items=[]`，没有 `current_value` |

原始响应保存在 `data/source-research/futu-opend-etf-valuation-2026-10-16/US.{symbol}-PE-YEAR_5.json`，附采集时间、请求参数、SDK 版本及 `research_only=true`。AAPL 控制组证明本机这条估值读取路径能够工作；ETF 返回成功但没有数据，不是显式权限拒绝，也不能证明其他窗口或 moomoo 独立账户必然有相同结果。

## Moomoo OpenD 实际试取

采集于北京时间 2026-10-16 18:37—18:38，通过本机 `127.0.0.1:11112` 的 Moomoo OpenD，只创建行情上下文。响应中的 `qot_logined=true`、服务版本 `1011`，SDK 版本 `10.11.7108`。只读取公开证券信息、行情和财务数据；未调用交易、账户资产或个人持仓接口。此时美国市场处于盘前，快照仅作字段覆盖核验，不将盘前时间或实时价格当作已收盘日线。

原始证据目录为 `data/source-research/moomoo-opend-etf-probe-2026-10-16/`，27 份响应 JSON 均带请求参数、观测时间、`provider=moomoo_opend` 和 `research_only=true`；全部返回 `ret=0`。另有 `summary.json` 保存统计和 27 份响应的 SHA-256。连接状态仅保存脱敏后的登录布尔值、版本和市场状态，不保存账号标识。

### 身份、估值和财务字段

`security-identity.json` 中三只 ETF 的代码、名称、`stock_type=ETF` 与目标相符，AAPL 为普通股票。三只 ETF 返回的 `listing_date=1970-01-01` 是不可采用的占位值，未用于历史起点。

| 查询 | VTI | VOO | QQQ | AAPL 对照 |
| --- | --- | --- | --- | --- |
| PE 详情，分别请求 1 / 5 / 30 年 | 三个窗口均 0 点，无当前 PE | 同左 | 同左 | 5 年返回 1,254 点，2021-10-18—2026-10-15，全部非零 |
| 快照证券类型 | `equity_valid=false`、`trust_valid=true` | 同左 | 同左 | `equity_valid=true` |
| 快照 `pe_ratio` / `pe_ttm_ratio` / `earning_per_share` | 全部 `null` | 全部 `null` | 全部 `null` | 分别为 45.844 / 39.22 / 7.46 |
| 财务关键指标，`statement_type=4,num=2` | 0 份报告，空字段结构 | 同左 | 同左 | 2 份报告，29 个字段 |
| 财务利润表，`statement_type=1,num=2` | 0 份报告，空字段结构 | 同左 | 同左 | 2 份报告，19 个字段 |

三只 ETF 的财务响应均为 `report_list=[]`、`structure_list=[]`、`next_key="-1"`，不是尚有分页未读。PE 详情的 `trend.historical_items=[]`，没有 `current_value`。AAPL 的当前估值详情为 39.22，5 年历史列表与前述富途 OpenD 对照逐项一致，但这不能据此确认所有证券的估值盈利口径，也不能算独立数据源核验。

这次是**查询成功但目标数据缺失**，未收到显式权限拒绝。登录、证券识别、ETF 价格和 AAPL 控制组都正常，因此不能把问题笼统归为 OpenD 未启动或账户未登录；同时，空结果本身也不能证明所有 moomoo 地区、账户或未来版本永远不覆盖这些数据。

### 历史 K 线：价格可用，但不能据字段存在生成 band

调用 `request_history_kline`，明确使用 `K_DAY`、`AuType.NONE`，只取日期、收盘价和 `PE_RATIO`；各响应均没有下一页。

| 证券及请求范围 | 返回日线 | PE 结果 |
| --- | --- | --- |
| VTI，2026-10-01—2026-10-15 | 11 条 | 全部为 0 |
| VOO，同上 | 11 条 | 全部为 0 |
| QQQ，同上 | 11 条 | 全部为 0 |
| VTI，2016-02-01—2016-08-15 | 137 条 | 117 条非零，其余 20 条为 0 |

VTI 非零片段实际为 **2016-02-19—2016-08-04，PE 4.047—4.634**。同日未复权收盘价除以 PE 得到约 24.0400—24.0459，几乎不变。该时间段、点数与数值范围和[先前富途 REST 试取记录](vti-official-data-research-2026-10-16.md)一致，仍没有持仓公司盈利口径或独立同口径数据证实，专用估值接口也没有对应历史。两品牌的近似结果不能当作独立来源验证。

这里的零值视为缺失，不计算无限 PE 或 EPS；孤立非零片段仅归档研究，不写入生产估值，也不据此补齐历史、生成 band 或恐慌线。此次只验证表内窗口，没有全量扫描三只 ETF 的全部历史。

`history-quota-before.json` 和 `history-quota-after.json` 证明历史 K 证券额度由已用 0 / 剩余 100 变为已用 3 / 剩余 97，只占用 VTI、VOO、QQQ 三只证券额度；同一 VTI 的第二个窗口没有再占用第四只。按[官方历史 K 说明](https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-history-kline.html)，当天占用的证券额度在 7 天后释放。

## 当前结论

moomoo OpenD 已实际连接成功，能够查询这三只 ETF 的价格；本次测试没有取得可支持持续指数估值 band 的 ETF 历史 PE/EPS。专用估值、快照及财务接口均未给出有效 ETF 估值，历史 K 仅出现一段未核实口径的 VTI PE。现有富途生产来源、暂停采集的美国全市场配置和数据库均保持原状，不用空值、零值或该孤立片段填补生产数据。
