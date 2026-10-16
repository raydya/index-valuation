# QQQ、VOO、VTI 的 Yahoo / Google 历史 PE 调研

核验日期：2026-10-16（北京时间）。对象严格为美股 ETF **QQQ、VOO、VTI**，不以 SPY、指数 PE 或其他市场挂牌份额替代。本文只记录公开资料和读取结果，不变更指数配置、采集逻辑或数据库。

## 官方资料所能确认的结论

**Google Sheets 的 GOOGLEFINANCE 不支持通过日期参数获取历史 PE。Yahoo 三只 ETF 的当前 PE 均可读取，但本次公开估值历史接口均未返回 PE 记录；同一接口查询普通股票则有带日期的 PE。Yahoo 确实有历史 PE 产品说明，但官方列明的估值历史范围是股票，尚不能确认 QQQ、VOO、VTI 三只 ETF 的付费历史 PE 下载能力。** 因而不能笼统地说“Yahoo 完全没有历史 PE”，也不能把 Gold 订阅的通用宣传当成 ETF 历史 PE 已获验证。

| 对象 | Google 官方历史查询能力 | Yahoo 免费公开能力的文档边界 | Yahoo 付费能力的文档边界 |
| --- | --- | --- | --- |
| QQQ | 历史属性不含 PE；即使当前 PE 可返回，也不代表历史 PE 可取 | 当前 PE 可读；历史 PE 接口无记录，Statistics 跳回首页 | Gold 宣传估值历史；ETF 覆盖、起点、频率和实际数值尚未获证实 |
| VOO | 同上 | 当前 PE 可读；历史 PE 接口无记录，Statistics 跳回首页；未用 SPY 替代 | 同上 |
| VTI | 同上 | 当前 PE 可读；历史 PE 接口无记录，Statistics 跳回首页 | 同上 |

以上能力边界分别依据 [Google 官方函数说明](https://support.google.com/docs/answer/3093281?hl=en)和 [Yahoo 官方订阅对比与覆盖范围 FAQ](https://finance.yahoo.com/about/plans/compare/)。这张表不以接口拒绝或网页限流推断供应商内部不存在数据。

## Google：当前 PE 与历史字段分开

[GOOGLEFINANCE 官方文档](https://support.google.com/docs/answer/3093281?hl=en)把 `pe` 放在当前数据属性列表；历史属性只有 `open`、`close`、`high`、`low`、`volume` 和包含这些字段的 `all`。文档明确：一旦指定任何日期参数，请求就是历史请求，只允许历史属性。因此，给 PE 查询加上起止日期并不能得到历史 PE。

该文档还说明部分属性并非适用于所有证券；所以“支持当前 PE 属性”不保证三只 ETF 都能返回当前 PE。日度/周度频率针对受支持的历史属性，不能解释为日度/周度历史 PE。Google 同时说明，GOOGLEFINANCE 历史结果不能通过 Sheets API 或 Apps Script 下载访问；因此不能把 Sheets 公式视为现成的程序化历史 PE 接口。[来源](https://support.google.com/docs/answer/3093281?hl=en)

本次未找到 Google 官方针对 QQQ、VOO、VTI 公布的付费历史 PE 服务或历史 PE API。这个结论是“未找到可验证的提供途径”，不是对 Google 内部数据存储的断言；也不排除未来产品变更。

## Yahoo：有股票历史 PE 产品，ETF 资格未证实

[官方订阅对比页](https://finance.yahoo.com/about/plans/compare/)的 Trailing P/E 一行，对免费、Bronze、Silver 标注五个季度，Gold 标注最长四十年；页面另列 Export Trailing P/E History。FAQ 将 Gold 的历史估值数据纳入可导出功能。这能确认 Yahoo 的历史 PE 产品确实存在，但“最长”不是每个证券的实际覆盖承诺，五个季度也不能当成日度历史。

同一页的证券覆盖 FAQ 区分：价格历史面向各类全球资产，而估值历史及财务报表历史的范围写为 **global stocks**。因此，虽然 Gold 的目标用户介绍包含 ETF 投资者，它没有明确承诺 QQQ、VOO、VTI 的 ETF PE 历史。三只 ETF 的可导出起始日期、实际采样频率、缺口、PE 的计算方法及是否与当前快照同口径，均仍需具体数据样本或 Yahoo 的明确答复才能确认。[来源](https://finance.yahoo.com/about/plans/compare/)

Yahoo 的[历史数据下载帮助](https://help.yahoo.com/kb/finance/certain-amounts-sln2311.html)说明下载需要 Gold，内容主要是价格、股息及拆股，并提示某些证券受数据许可限制而没有下载按钮。该页不能证明 ETF 的 PE 历史也可下载。另一份[投资研究页面说明](https://help.yahoo.com/kb/SLN28276.html)也把历史数据页描述为价格、拆股和股息，而把估值指标放在 Statistics；并明确页面是否存在取决于证券。

官方订阅说明中的数据导出不等于公开、稳定、免认证的 API 合约。本次未登录付费账户，也没有订阅、购买或尝试绕过访问限制，故不声称已经验证付费 ETF PE 历史不可用或可用。

## 公开页面和接口实测

实测时间为北京时间 **2026-10-16 09:31—09:32**，使用不带登录凭据的普通 HTTP 请求。没有使用 Chrome、付费账户、Google Sheets 或 Google Drive。以下响应已保存原文，数值解析使用 `Decimal`。

### Yahoo 当前快照与历史价格

| ETF | 行情页的 `summaryDetail.trailingPE.raw` | `key-statistics/` 请求结果 | `history/` 请求结果 |
| --- | ---: | --- | --- |
| [QQQ](https://finance.yahoo.com/quote/QQQ/) | 27.87565 | 最终跳至 QQQ 行情首页，HTTP 200 | HTTP 200；默认区间包含 251 个价格时间戳 |
| [VOO](https://finance.yahoo.com/quote/VOO/) | 24.954075 | 最终跳至 VOO 行情首页，HTTP 200 | HTTP 200；默认区间包含 251 个价格时间戳 |
| [VTI](https://finance.yahoo.com/quote/VTI/) | 24.300972 | 最终跳至 VTI 行情首页，HTTP 200 | HTTP 200；默认区间包含 251 个价格时间戳 |

三只证券均确认 `quoteType = ETF`。行情时间均为 `2026-10-15T20:00:00Z`，即纽约 10 月 15 日 16:00 收盘；**这只是行情时间，并非供应商明确给出的 PE 生效日期**。PE 为本次采集的快照，不能据此确定历史有效区间。

历史页内嵌 `chart` 的逐日字段为 open、high、low、close、volume，另有复权价格及事件信息；没有逐日 PE 字段。历史页还包含与行情页相同的当前 `summaryDetail.trailingPE`，不能把这个单值误当成每个历史日期对应的 PE。251 是默认页面区间的价格记录数，不代表价格全部历史只有这些记录。

### Yahoo 估值历史接口与对照

先读取 [AAPL 的 Statistics 页面](https://finance.yahoo.com/quote/AAPL/key-statistics/)，从公开 HTML 的 `data-url` 获取其实际引用的 `fundamentals-timeseries` 请求。再使用同一接口、同一日期范围，仅替换证券代码并选取 `quarterlyPeRatio,trailingPeRatio` 两个字段：

```text
https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}
  ?merge=false&padTimeSeries=true
  &period1=493590046&period2=1792115999
  &type=quarterlyPeRatio,trailingPeRatio&lang=en-US&region=US
```

范围沿用网页请求，覆盖 1985-08-22 至 2026-10-16 UTC，包含三只 ETF 的整个存续期。使用这段范围不意味着免费账户可获得该范围全部数据；实际覆盖以响应为准。

| 查询对象 | HTTP / API 错误 | `quarterlyPeRatio` 有值记录数 | `trailingPeRatio` 有值记录数 |
| --- | --- | ---: | ---: |
| QQQ | 200 / `error: null` | 0 | 0 |
| VOO | 200 / `error: null` | 0 | 0 |
| VTI | 200 / `error: null` | 0 | 0 |
| AAPL（普通股票对照） | 200 / `error: null` | 5 | 8 |

三只 ETF 均只有证券代码、指标名的 `meta`，没有 `timestamp` 和带日期的 PE 记录。AAPL 返回的季度记录覆盖 2025-06-30 至 2026-06-30，TTM 记录覆盖 2024-06-10 至 2026-09-17、日期不连续。该对照确认请求方式有效，同时也说明不能仅凭字段名把返回序列认定为完整日度数据。

本次结果支持“**这些公开请求没有取得三只 ETF 的历史 PE**”；不能扩大为“Yahoo 内部或所有付费产品绝无 ETF 历史 PE”。没有得到任何可用于三只 ETF 历史回填的 PE 样本。

### Google 页面

直接读取 [QQQ:NASDAQ](https://www.google.com/finance/quote/QQQ:NASDAQ?hl=en)、[VOO:NYSEARCA](https://www.google.com/finance/quote/VOO:NYSEARCA?hl=en)、[VTI:NYSEARCA](https://www.google.com/finance/quote/VTI:NYSEARCA?hl=en)，三页均 HTTP 200，标题对应目标 ETF。公开页面未显示 PE 数值或历史 PE 表。历史 PE 不受支持的确定依据仍是上文 Google 官方函数文档，而不是仅凭页面里没找到文字。

### 本地证据

- `data/source-research/etf-pe-history-2026-10-16.json`：三只 ETF 的 Yahoo 行情、Statistics、历史页，Google 行情页，以及 Yahoo 官方计划比较页的 URL、跳转、HTTP 状态和原始响应路径。
- `data/source-research/etf-pe-observed-fields-2026-10-16.json`：当前 PE、ETF 身份、行情时间、采集时间与价格字段。
- `data/source-research/etf-pe-timeseries-2026-10-16.json`：三个 ETF 及 AAPL 对照的完整请求 URL、响应与原始归档路径。
- `data/raw/etf_pe_history_probe/`：以上公开响应的按内容哈希归档；AAPL 页面证据为 `bb9c2ad233f44a779f6896d5b096a9afe08c6a25ded61e833733f87d7b1fee03.json`，Yahoo 计划页为 `deabacbe45a5b1bde3ab9d8ae2114cb892857b74ad8dca95f3327a15649ab042.json`。

上述 `data/` 文件按项目约定不纳入 Git。未执行采集入库、代理切换或派生序列重建。

## 证据与复核限制

- Google 函数文档本次可直接读取。Yahoo 订阅页通过网页检索工具直开曾返回 429，随后普通 HTTP 请求返回 200，正文与官方搜索索引的产品说明一致，已归档；部分帮助页采用官方域名的搜索索引。没有在登录后的付费产品页面完成操作验证。
- 搜索中出现的财经新闻、第三方评论、其他 ETF、股票和海外挂牌代码均未作为三只目标 ETF 的覆盖证据。
- 如需进一步确认 Gold 能否解决回填问题，所缺证据是 QQQ、VOO、VTI 各自的历史估值下载样本或供应商明确支持答复，包含 PE 字段含义、观察日期、频率和覆盖范围。仅升级订阅、看到通用下载按钮或取得历史价格不足以完成验证。

## 对本项目的含义

在取得真实历史 PE 序列前，应继续将已读取的 ETF PE 作为带采集时间的当前快照逐日积累，不把当前 PE 复制到历史，不用历史价格除以当前 EPS 拼出所谓历史 PE。此次调研不会将现有 SPY 代理配置改成 VOO；VOO 是本次独立核验对象。
