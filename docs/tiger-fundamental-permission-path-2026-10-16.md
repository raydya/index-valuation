# 老虎美股基本面权限申请路径与 ETF 覆盖边界

核验日期：2026-10-16。范围为官方公开文档、官方 Python SDK 源码及项目已有实测记录，后追加对本轮新 K 线响应的独立只读审核；本项没有读取凭据、登录账户、调用用户 API、联系客服或购买服务。

**本轮已有突破：官方 K 线接口启用 `with_fundamental=true` 后，VTI、VOO、QQQ 均已取得 2022-09-09—2026-10-15 的 1,029 条非零 TTM PE；近期三只 ETF 的未复权收盘价已通过 moomoo 归档逐日核验。专用基本面接口仍受权限限制，不能把它扩大表述为“老虎没有可用 PE 接口”。** 三只 ETF 的当前 PE 样本已强匹配基金自身投资损益，而非持仓公司利润；VTI / VOO 的 `ttmPe` 对应 2024 财年、`lyrPe` 对应 2025 财年，不能把字段名当作最新盈利期间的证明。这些值保留研究，不直接进入指数 band。

下一步应先向官方确认是否另有持仓公司穿透估值产品，不能假设开通同源基金会计基本面权限即可满足需求。已有 K 线 PE 无需等待新授权即可研究；若需要直接 EPS、报表明细，官方权限申请途径是联系客服。公开资料没有证明购买美股 L1/L2 就能开通基本面服务。完整请求及历史统计见[本机实测记录](tiger-account-probe-2026-10-16.md)，会计证据见 [QQQ 核验](tiger-etf-pe-basis-2026-10-16.md)、[VTI / VOO 核验](tiger-vti-voo-pe-basis-2026-10-16.md)。这些是强数值匹配，仍未获 Tiger 算法正式确认。

## 新 K 线响应的独立审核

证据目录：`data/source-research/tiger-probe-2026-10-16/market-followup-20261016T104802Z/`，文件 `rsa-{VTI,VOO,QQQ,AAPL}-kline-with-fundamental.json`。实际请求 `period=day`、`right=nr`、`with_fundamental=true`、起始 `2026-10-01`、结束 `2026-10-16`。四响应均为 HTTP 200、业务 code 0；归档记录 HTTPS 与官方 SDK 规则 RSA 响应验签通过。审核没有重新读取密钥或重复网络请求。

| 证券 | 实际日期范围 | 行数 / 正 TTM PE 数 | 2026-10-15 close | 当日 `ttmPe` | 同期 moomoo 未复权价格核验 |
| --- | --- | --- | --- | --- | --- |
| VTI | 2026-10-01—2026-10-15 | 11 / 11 | 378.42 | 9.062088 | 11/11 完全一致 |
| VOO | 2026-10-01—2026-10-15 | 11 / 11 | 709.43 | 7.869659 | 11/11 完全一致 |
| QQQ | 2026-10-01—2026-10-15 | 11 / 11 | 728.00 | 6.496715 | 11/11 完全一致 |
| AAPL | 2026-10-01—2026-10-15 | 11 / 11 | 342.00 | 39.233346 | 本次未作该证券跨源价格比较 |

原始 `response.data[]` 包含正确证券 `symbol` 和 `period=day`，其 `items[]` 内同时提供 OHLC、`time`、`ttmPe`、`lyrPe`。各证券日期唯一、升序，毫秒时间戳换算为 `America/New_York` 后全部为当地零点，实际落在该区间 11 个工作日；没有将 UTC 或北京时间日期直接用作美股交易日。`ttmPe` 与 `lyrPe` 均非零且为正。此处时间是日 K 日期标签，不能直接认定为 EPS 披露或生效时间。

价格对照来自 `data/source-research/moomoo-opend-etf-probe-2026-10-16/US.{VTI,VOO,QQQ}-history-2026-10-01-2026-10-15.json`，其 `autype=NONE`。使用 `Decimal` 对比 33 个同日收盘值，全部精确相同，两个来源各自没有未匹配日期。该结论只覆盖这段日期；moomoo 的 PE 全为零，因此不能提供 PE 核验，也不能据此宣称两个渠道具有独立上游。

同区间 `close/ttmPe` 各自近似固定，VTI 约 41.758587、VOO 约 90.147490、QQQ 约 112.056626。这是研究反算值，不是接口直接返回的 EPS。后续发行人原始报表验证，VTI / VOO 分别与 2024 财年运营损益除以全部份额类别合计对应，QQQ 与 2025 财年运营损益除以期末份额对应。相关损益包含已实现及未实现投资涨跌，不是持仓公司净利润；VTI / VOO 还存在不同净值单位份额直接合计的问题。详见上述两个专项记录。

后续七段长历史请求及重叠短窗口复核，已经把三只 ETF 的 PE 扩展到各 1,029 条。首轮 2022-12-30 缺值在跨年短窗口真实返回；短窗末条 2023-01-04 和另一短窗末条 2022-09-16 又缺 PE，但两天在原长窗有值。这是实际请求边界行为：合并同日真实非空观测、保留已知值，不进行前向填充。重叠补采后 2022-09-09—2026-10-15 按本地 NYSE 日历无缺日，2021 年独立样本仍无 PE。最终价格行数为 VTI 6,370、VOO 4,050、QQQ 6,944；短窗只补 PE，历史价格异常清单保留在 `summary.json`。研究 CSV 位于 `data/exports/tiger-etf-pe-research-2026-10-16/`，不代表生产接入或成立以来完整历史。

## 权限分层

| 层级 | 官方说明 | 对本任务的含义 |
| --- | --- | --- |
| OpenAPI 接入 | 个人通常完成开户、入金后申请 API；支持 RSA 或 OAuth | 这是接入资格；已有 RSA 成功响应，不需要为相同接口反复更换认证方式 |
| OAuth 应用授权 | OAuth 不替代账户已有的 API、行情或交易权限 | 再授权 `api.quote:read` 不能自行补上缺失的数据产品权限 |
| 实时行情 | API 行情独立于 APP/PC；个人可在开发者信息页或 Tiger Trade「我的 → 行情权限商店 → API」购买 | L1/L2 提供报价、盘口、成交；不能当作基本面权限的同义词 |
| 历史 K 线额度 | 开通 API 有基础股票/ETF 数量额度；更高资产/成交额或额外购买可提高额度 | 额度是可请求证券数量，不是历史 PE 数据授权证明 |
| 专用基本面数据 | `get_financial_report`、`get_stock_fundamental`、`get_financial_daily` 默认不开通，须联系客服申请 | 对应 `US financial service` 权限错误；不覆盖本轮已经成功返回 PE 的 K 线接口 |

依据：[接入概述](https://docs.itigerup.com/docs/intro)、[认证方式](https://docs.itigerup.com/docs/authentication)、[行情费用](https://docs.itigerup.com/docs/fee)、[行情权限与额度](https://docs.itigerup.com/docs/permission)、[FAQ 的基本面权限说明](https://docs.itigerup.com/docs/faq)。

公开文档未披露基本面服务统一价格、最低资产、是否仅向特定地区/客户类别提供、试用期限或保证开通时效。不能把普通 API 接入门槛、实时行情价格或 K 线额度门槛套用为该服务的收费和资格条件。没有找到基本面服务的公开自助开通按钮；准确表述应为“官方文档指定联系客服申请”，而非断言后台绝对不存在其他入口。

## 申请步骤与可直接使用的说明

1. 用户在自己的[开发者中心](https://developer.itigerup.com/)确认 Tiger ID；无需重新生成密钥。
2. 使用老虎官方 API 支持渠道。官方[联系我们](https://docs.itigerup.com/docs/contact)提供量化 QQ 群二维码及 [Telegram API 支持群](https://t.me/TigerBrokersAPISupport)。普通券商客服也可协助转接 API 团队；公开文档没有公布一个单独的基本面权限自助表单。
3. 携带下述会计匹配证据，优先询问是否另有持仓穿透估值产品，再确认权限、覆盖及费用；同源基金会计数据不自动满足需求。不需要提交私钥或完整配置文件；有需要时仅向官方私密客服提供 Tiger ID 和去敏错误信息，不在公开群发布账户资料。
4. 产品适用性及开通均确认后，先用 AAPL 与 VTI 的短区间验证实际字段，再扩展 VOO、QQQ 和早期样本，不直接跑全历史。收费事项先取得报价再决定。

以下申请文案尚未发送：

> 我已开通 Tiger OpenAPI，RSA 认证可用。`stock_fundamental` / `financial_daily` 报 US financial service 权限错误，正式日估值字段 `p_to_ltm_diluted_eps_before_extra` 也一样；但 `kline(with_fundamental=true)` 已返回 VTI、VOO、QQQ 的历史 PE。
>
> 2026-10-15 的样本与基金自身运营损益匹配：VTI close=378.42、ttmPe=9.062088，分母可由 2024 年 346153203000 / 全类别份额 8289389590 重建；VOO close=709.43、ttmPe=7.869659，分母可由 2024 年 254533400000 / 2823521698 重建。两只基金 lyrPe 则对应 2025 年。QQQ close=728、ttmPe=6.496715，与 2025 财年运营损益 71901131802 / 份额 641650000 匹配到返回精度。
>
> 请确认这些字段的实际算法、盈利期间与上游数据，重点告知是否另有按持仓公司利润穿透计算的 ETF PE/EPS 或指数估值产品；其亏损处理、权重、价格口径、最早日期、更新和修订规则是什么？若 US financial service 仍提供同源基金会计数据，仅开通权限不能满足需求。请先说明适用产品、个人资格、费用与试用方式；只需数据查询，不需交易权限，不直接开通收费服务。

## 官方 SDK 实际可查询能力

| 接口 | 官方源码确认的能力 | 尚未确认 |
| --- | --- | --- |
| `get_stock_fundamental(symbols, market, sec_type=None)` | 当前基本面示例包含 `ttm_pe_rate`、`ttm_eps`、`lyr_pe_rate`、`lyr_eps` | ETF 是否返回；每个字段的基金会计/持仓穿透口径；没有历史日期参数 |
| `get_financial_daily(symbols, market, fields, begin_date, end_date, timezone=None)` | 按日期查询；返回 `symbol/field/date/value` 长表 | VTI、VOO、QQQ 覆盖、最早日期、是否交易日采样 |
| `get_financial_report(..., period_type, ...)` | 支持 LTM 等报告周期；EPS 字段见官方枚举 | 是否有独立持仓穿透产品；同源基金自身报表并不满足需求 |
| `get_bars(..., with_fundamental=True)` | 三只 ETF 各取得 1,029 条 PE，当前样本强匹配基金运营损益；AAPL 另有 11 日对照 | Tiger 正式算法确认、全历史期间和修订规则、2022-09-09 以前覆盖；不能把当下成功推广为所有账号权限规则 |

方法依据：[官方 QuoteClient 源码](https://github.com/tigerfintech/openapi-python-sdk/blob/master/tigeropen/quote/quote_client.py)。这是当前公开主分支，后续实施仍需核对实际使用的 SDK 版本。

**字段名存在官方资料不一致，应按 SDK 与真实响应验证。** 官方[行情参考 skill](https://github.com/tigerfintech/tigeropen-skill/blob/main/skills/tigeropen/references/quote.md)示例把日财务字段写为 `pe_ttm`、`eps_ttm`；但本次读取的[官方估值枚举源码](https://github.com/tigerfintech/openapi-python-sdk/blob/master/tigeropen/common/consts/fundamental_fields.py)中，`Valuation` 使用 `p_to_ltm_diluted_eps_before_extra`，未列出前述两个短名；利润表枚举有 `basic_eps`、`diluted_eps_excl_extra_items` 等。后续对 VTI、AAPL 使用正式字段 `p_to_ltm_diluted_eps_before_extra` 复测，仍明确返回 1010 US financial service 权限拒绝。旧短名没有成功响应证据，不继续推荐为已验证字段。

## ETF 支持证据与口径限制

官方行情权限页明确股票包括 ETF，说明可请求 ETF 行情，不等于历史 PE 或 EPS 都有数据。官方[选股器](https://docs.itigerup.com/docs/quote-scanner)有 ETF 类型标签、PE/EPS 字段，但没有历史日期查询能力，也不保证每条结果返回所请求字段。不能用“选股器支持 ETF”推导“ETF 历史 PE 已获支持”。

官方[基金接口](https://docs.itigerup.com/docs/quote-fund)示例采用 `ISIN.币种` 基金代码，历史接口返回净值及日期，没有 PE/EPS 字段。这不是已证实的 VTI 历史估值替代入口；官方 skill 中用 ARKK 作为基金接口示例，也不能覆盖正式接口文档对实际证券分类的说明。

本轮未找到官方对 VTI、VOO、QQQ 历史持仓穿透 PE/EPS 的明确产品承诺或加权/亏损处理公式。当前状态是：专用基本面仍受限；K 线 PE 已取得；三只 ETF 当前字段与基金投资损益有强数值匹配，故不直接用于持仓公司盈利估值；尚未获得 Tiger 对内部算法和全历史一致性的正式确认。具体证据见 [QQQ](tiger-etf-pe-basis-2026-10-16.md)、[VTI / VOO](tiger-vti-voo-pe-basis-2026-10-16.md)。
