# 长桥 ETF PE / EPS：官方 SDK 路径复查

核查日期：2026-10-16。目标是寻找此前 `/v1/quote/valuation` 与 `/detail` 之外的 VTI、VOO、QQQ 估值入口。本文件记录公开文档和源码能证明的接口能力；不把方法存在等同于账户获权、ETF 有数据或盈利口径正确。本文研究没有读取凭据、发起认证市场请求、使用浏览器或修改生产配置。

任务收尾状态：主任务已对新增 GET 路径完成另行认证试取，返回的 ETF EPS / 净利润仍指向旧基金自身投资损益，未解决持仓公司盈利口径问题。下文 `POST /quote/static-info`、`/quote/quotes`、`/quote/calc-indexes` **仅完成官方源码核查，本轮没有认证实测**；它们的公开能力为当前快照，不能提供缺少的历史持仓 PE。

## 优先验证的新增入口

| 入口 | 请求参数 | 返回能力与边界 |
| --- | --- | --- |
| `GET /v1/quote/compare/valuation` | `symbol=VTI.US&currency=USD`；可选重复参数 `comparison_symbols` | 每项有当前 `pe`、`eps`、`price_close`，以及 `history[].date/pe/pb/ps`。是独立于旧估值图端点的历史候选；没有公开 start/end 参数，实际采样、覆盖和 ETF 数据须实测。 |
| `GET /v1/quote/financial-reports` | `symbol=VTI.US&kind=IS&report=af` | 公司报表；`kind` 为 IS/BS/CF/ALL，`report` 可选 af/saf/q1/q2/q3/qf/3q。报表 EPS 不能自动解释成 ETF 持仓 EPS。 |
| `GET /v1/quote/financials/statements` | `symbol=VTI.US&kind=IS&report=af` | 另一套明细报表路径；这里 kind 只允许 IS/BS/CF，ALL 或缺失可能返回空，不应误判没有财务数据。 |
| `GET /v1/quote/financials/latest-report` | `symbol=VTI.US` | 最新财报指标摘要；可协助核对 EPS 财期，不提供每日 PE。 |
| `GET /v1/quote/financials/earnings-snapshot` | `symbol=VTI.US`；可选 `report=af`、`fiscal_year=2025`、`fiscal_period` | `fo_eps.value` 与 `est_value` 是实际值与预测对照，须分别读取；带财期信息。不能把预测值作为 TTM 盈利。 |

估值比较见[官方文档](https://open.longbridge.com/docs/fundamental/fundamental/valuation-comparison)和 [SDK 实现](https://github.com/longbridge/openapi/blob/4cfc453e36ea9936f6a04c647830127e1218716a/rust/src/fundamental/context.rs)。该实现把历史 `date` 作为 Unix 秒转换为 RFC 3339；原始响应仍需保留，不能假设日期就是财务生效日。财报说明见[Financial Report](https://open.longbridge.com/docs/fundamental/fundamental/financial-report)、[Financial Report Snapshot](https://open.longbridge.com/docs/fundamental/fundamental/financial-report-snapshot)。明细和 latest 路径见[官方 CLI 源码](https://github.com/longbridge/longbridge-terminal/blob/9120d0fddd1c47b3087b280b0f2742ec413847a9/src/cli/fundamental.rs)。上述示例均可替换为 VOO.US、QQQ.US，并以 AAPL.US 作普通公司控制组。

## 静态 EPS 与当前 PE：新增的 HTTP 传输

长桥 SDK **5.2.1（2026-10-14）**新增 `QuoteTransport::Http`，原 WebSocket pull 查询可走 `POST /quote/*`。这些 POST 是报价查询，不是交易写入。可复用已授权 OAuth Bearer 直接请求，不需要 App Key，也不必另开 WebSocket。[官方更新日志](https://github.com/longbridge/openapi/blob/4cfc453e36ea9936f6a04c647830127e1218716a/CHANGELOG.md)

```text
POST /quote/static-info
{"symbol":["VTI.US","VOO.US","QQQ.US","AAPL.US"]}

POST /quote/quotes
{"symbol":["VTI.US","VOO.US","QQQ.US","AAPL.US"]}

POST /quote/calc-indexes
{"symbols":["VTI.US","VOO.US","QQQ.US","AAPL.US"],"calc_index":[1,12,13]}
```

注意：前两项为 `symbol` 数组，第三项为 `symbols` 数组。`calc_index` 的 1、12、13 分别为 LastDone、PeTtmRatio、PbRatio。路径来自[官方指令映射](https://github.com/longbridge/openapi/blob/4cfc453e36ea9936f6a04c647830127e1218716a/rust/src/quote/cmd_code.rs)，JSON body 与 WebSocket protobuf 请求相同，HTTP 封装见[QuoteContext](https://github.com/longbridge/openapi/blob/4cfc453e36ea9936f6a04c647830127e1218716a/rust/src/quote/context.rs)。

- `static-info` 提供 `eps`、`eps_ttm`、总份额、币种、交易所等当前基础字段；没有查询历史日期的参数，也没有 EPS 的单独生效时间。[Static Info 文档](https://open.longbridge.com/docs/quote/pull/static)
- `calc-indexes` 提供当前 `pe_ttm_ratio`。某些 CLI 示例提到 EPS，但正式 CalcIndex 枚举没有 EPS 项；EPS 应从 static-info 取，不能猜造 `calc_index` 编号。[Calc Index 文档](https://open.longbridge.com/docs/quote/pull/calc-index)、[正式 protobuf](https://github.com/longbridge/openapi-protobufs/blob/main/quote/api.proto)
- HTTP gateway 可能使用 camelCase 键、字符串数字，并省略默认值或返回 null。研究保存原始值，不能沿用 SDK 默认值转换把缺失 EPS 变成真实零。HTTP 业务错误可能表现为外层 500，需读取业务 code。
- 官方更新日志指出 HTTP 的 US 夜盘快照可能与默认 WebSocket 快照不同；取得当前价格和 PE 后也不能直接标成最近正式收盘日。

Rust 提供 `OAuth::from_token(access_token)`：可从已有内存令牌构造客户端，不执行登录、不续期。Python OAuth 绑定目前只暴露 Builder，默认会读写 `~/.longbridge/openapi/tokens/<client_id>`；没有对应 from_token 方法。此次若已有内存 Bearer，直接走上述 HTTP 查询比为 SDK 再次授权更合适。[Rust OAuth 源码](https://github.com/longbridge/openapi/blob/4cfc453e36ea9936f6a04c647830127e1218716a/rust/crates/oauth/src/client.rs)、[Python OAuth 绑定](https://github.com/longbridge/openapi/blob/4cfc453e36ea9936f6a04c647830127e1218716a/python/src/oauth.rs)

## ETF 专用接口与不能替代的能力

`GET /v1/quote/etf-asset-allocation?symbol=VTI.US` 返回持仓、地区、资产类别、行业分组；每组有报告日期、每项有持仓权重。公开类型未定义穿透 PE/EPS，也没有历史日期参数。CLI `constituent` 对美国 ETF 优先从 SEC N-PORT 取完整持仓，不可据此称其有长桥的历史组合盈利；失败才回退平台资产配置。[官方 constituent 文档](https://open.longbridge.com/docs/cli/market-data/constituent)

`fund-holders` / 文档中的 fund-holdings 是反向“哪些基金持有此证券”，不是指定 ETF 的全部成分。新 `FundContext` 面向 UT/FD 类型共同基金的 NAV、收益、资产配置等渠道，不能将 `VTI.US` 直接当成该渠道基金标识。[基金持有人文档](https://open.longbridge.com/zh-HK/docs/fundamental/fundamental/fund-holdings)、[FundContext 更新说明](https://github.com/longbridge/openapi/blob/4cfc453e36ea9936f6a04c647830127e1218716a/CHANGELOG.md)

历史 K 线的正式返回只有 OHLC、量额、时间等行情字段，没有 Tiger 式 `with_fundamental` 参数或历史 PE/EPS。不能把其他券商的参数照搬到长桥。[Historical Candlesticks 文档](https://open.longbridge.com/docs/quote/pull/history-candlestick)

`GET /v1/quote/valuation/rank` 是按日期查询行业内 `rank/total`，不提供可直接用作 PE 的排名数值。`forecast-eps`、consensus 属于分析师预测，应与已实现盈利分开。[官方 CLI 实现](https://github.com/longbridge/longbridge-terminal/blob/9120d0fddd1c47b3087b280b0f2742ec413847a9/src/cli/fundamental.rs)

## AP / US 数据中心边界

官方 SDK 还包含 US 专用路径：`/v1/us/stock-info/valuation-overview`、`finn-overview`、`fin-keyfactor`，以及 `/v1/us/quote/financials/statements`。它们明确要求 US 数据中心 token；`report` 参数为 `annual` / `quarterly`，与 AP 的 af/qf 不同。US 股票代码不意味着账户处于 US 数据中心，不能为测试伪装 token 区域或绕过地域限制。[官方 US 接入更新说明](https://open.longbridge.com/docs/changelog)、[SDK 区域校验与请求实现](https://github.com/longbridge/openapi/blob/4cfc453e36ea9936f6a04c647830127e1218716a/rust/src/fundamental/context.rs)

## 本次发现与未解决事项

旧历史图的常量序列不足以代表整个长桥平台；本次找到估值比较、当前报价指标、报表明细等独立核验路径。主任务对新增 GET 的实测仍没有取得适用于 band 的持仓盈利数据。接口层面也未找到明确承诺“ETF 持仓公司盈利加权 PE/EPS 历史”的专门字段。报价 POST 保留为未实测的当前字段查询候选，不把它当成已找到的历史解决方案。

公开源码归档在 `data/source-research/longbridge-sdk-routes-2026-10-16/`。固定核查版本：openapi `4cfc453e36ea9936f6a04c647830127e1218716a`；longbridge-terminal `9120d0fddd1c47b3087b280b0f2742ec413847a9`。历史账户实测背景见[券商估值研究](broker-etf-valuation-research-2026-10-16.md)，本轮真实认证响应应另行记录，不能由本文的源码发现代替。
