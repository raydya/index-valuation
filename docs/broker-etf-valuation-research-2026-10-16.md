# 长桥、老虎的 ETF PE / EPS 与权限核验

核验日期：2026-10-16。范围为 VTI，兼查 VOO、QQQ；研究期间未更改应用数据源、数据库或采集配置，未购买或联系券商。用户随后确认已有两家账户并要求验证 OpenAPI 权限，因此本轮注册了临时 OAuth public client，由用户登录官方页面完成授权，并按用户许可执行只读核验；这不等于注册券商账户。令牌不写入项目或报告，账户权限以本轮实际查询为准。

## 结论与证据等级

两家都有历史基本面接口，不能笼统判断“券商都没有历史 PE”。但**接口存在、账户获权、ETF 有返回、返回是持仓盈利口径、数据持续更新**是独立条件。本轮已通过长桥用户授权取得 VTI、VOO、QQQ 的 PE 历史；三个序列末尾均有连续 131 周数值完全不变，且公开 QQQ EPS 已找到基金自身投资损益口径的原报证据，因此尚不能用于本项目的指数盈利 band。老虎的专用基本面服务仍拒绝访问，但普通日 K 线启用 `with_fundamental=true` 已取得三只 ETF 各 1,029 条历史 PE。继续核对基金原始年报后，近期 PE 高度匹配基金自身投资损益口径，也不适合直接作为持仓公司盈利估值。

| 平台 | 已确认接口 | ETF 样本 | 当前判断 |
| --- | --- | --- | --- |
| 长桥 | 原估值图、`compare/valuation`、财报/快照、ETF 配置；另有发行人事实表 | 原图长尾 131 周常量；比较接口每只仅 9 个负 PE；直接 EPS 已匹配旧基金投资损益；VTI/VOO 事实表另有 2026-03-31 组合 PE | 常规估值/财报不适合生产 band；发行人文档提供有日期单点，尚无连续历史 |
| 老虎 | `get_bars(with_fundamental=True)` 已成功；专用 `get_financial_daily` / `get_stock_fundamental` 仍受限 | 三只 ETF 各 1,029 条非零 PE，2022-09-09—2026-10-15 | 近期值与基金自身投资损益数值对应；专用财务权限与持仓盈利口径是两个独立问题 |

## 长桥：接口与公开页面证据

[历史估值文档](https://open.longbridge.com/docs/fundamental/fundamental/valuation-history)提供 Python、Rust 等 SDK 方法，`history.metrics.pe.list` 是历史点列，不是单个当前快照。文档示例证券是 AAPL，不能据此承诺 VTI 支持。

[官方 Rust SDK 源码](https://github.com/longbridge/openapi/blob/main/rust/src/fundamental/context.rs)确认以下路径：

```text
GET /v1/quote/valuation?symbol=VTI.US&indicator=pe&range=1
GET /v1/quote/valuation/detail?symbol=VTI.US
```

[CLI 估值文档](https://open.longbridge.com/docs/cli/fundamentals/valuation)列出 `--history --indicator pe --range 5`，`--range` 可选 1/3/5/10 年。但返回频率、实际覆盖、ETF 是否非空必须以响应为准。SDK 的 `valuation_history` 方法只有 symbol 参数，不能假设它与 CLI 的窗口参数完全相同。文档示例使用 `timestamp`，schema 又写 `date`，接入时需要以真实响应核验日期格式。

[更新日志](https://open.longbridge.com/docs/changelog)将 `FundamentalContext` 列为 2026-05-13 的 SDK 4.1.0 新增能力。快速开始中旧的 4.0.5 示例不是验证此功能的合适版本基线。旧包 `longport` 已弃用，当前包名 `longbridge`，见[快速开始](https://open.longbridge.com/docs/getting-started)。

一次不带凭据的只读请求：

```text
GET https://openapi.longbridge.com/v1/quote/valuation/detail?symbol=VTI.US
HTTP 401
{"code":401001,"message":"token empty","data":null}
```

这是未授权，**不是“ETF 不支持”的返回**。

公开网页的 HTTP HTML 中可读取嵌入的行情 JSON，实取结果如下；仅代表此次观测，不是历史 EPS 样本，也不是独立核验通过：

| 官方页面 | 身份 | `eps_ttm` | 其他相关字段 |
| --- | --- | --- | --- |
| [VTI](https://longbridge.com/en/quote/VTI.US) | `ETF/US/VTI`，`security_type=ETF` | 25.659949 | `eps=-32.690164`、`eps_forecast=45.006538` |
| [VOO](https://longbridge.com/en/quote/VOO.US) | `ETF/US/VOO` | 58.050577 | `eps=-66.207644`、`eps_forecast=103.1217` |
| [QQQ](https://longbridge.com/en/quote/QQQ.US) | `ETF/US/QQQ` | -88.498502 | `eps_forecast=-88.498502` |

这些字段未给出各自财务生效日。行情 timestamp 不可冒充财务生效时间。官方[股票字段解释](https://support.longportapp.com/topics/base/stock-fields)定义普通公司 TTM EPS 为过去 12 个月净利润除最新总股本；该页没有定义 ETF 持仓盈利汇总。因此不能直接将以上 ETF EPS 当作投资组合 EPS，或据之称 VTI 市场整体只有十几倍 PE。

QQQ 的公开字段有进一步会计证据：[Invesco 2022 财年原报（SEC）](https://www.sec.gov/Archives/edgar/data/1067839/000119312523017732/d365144d485bpos.htm)的基金 operations 净资产减少为 `-48,948,521,639` 美元，除以期末 `553,100,000` 份得到 `-88.4985023305`，与页面 `eps_ttm=-88.498502` 对应。这里包含基金自身投资的未实现市价损益，不能当作 ETF 持仓公司的汇总盈利。这项精确对应仅针对公开 QQQ 字段；不据此断言所有券商 ETF 数值都来自同一财年或同一计算方式。

完整会计核对与适用边界见[ETF 会计 EPS 专项核验](etf-accounting-eps-validation-2026-10-16.md)。

## 长桥：授权后的 20 次只读市场数据实测

第三轮取得的实际 OAuth scope 为 `watchlist`，response 与 JWT 一致，没有 account/trade scope。在用户已明确允许读取数据的范围内完成 20 次固定 GET：四个证券的 valuation 与 valuation/detail 共 8 次，再对四个证券请求 valuation 的 range=3、5、10 共 12 次，均 HTTP 200、`code=0`。未读取账户、资产、持仓或订单，未订阅行情或交易。原始响应按证券和 range 归档，统计见 `data/source-research/longbridge-probe-2026-10-16/history_coverage_summary.json`。

请求以 AAPL.US 为普通股票控制组；ETF 身份由官方公开页面确认，detail 的 `overview.aichat_data.symbol` 与请求代码一致。字段名为 `pe`，未给出足以核实 ETF 盈利期间、持仓权重及亏损处理的方法说明，**不能仅凭此字段名认定为 TTM 持仓 PE**。

| 请求窗口 | 三只 ETF 实际 PE 覆盖 | 频率及限制 |
| --- | --- | --- |
| range=1 | 三只均 `pe.list=[]` | HTTP 成功不等于有可用数据 |
| range=3 | 各 156 点，2023-10-23—2026-10-12 | 每 7 天一个点，时间戳转纽约日期均为周一 |
| range=5 | 各 261 点，2021-10-18—2026-10-12 | 周度样本，不是日线 |
| range=10 | 各 61 点，2021-10-01—2026-10-01 | 每月 1 日标记；实际仅约 5 年，不能称 10 年历史 |

上述日期忠实按返回 Unix timestamp 和 `America/New_York` 解码，不自行移动到相邻交易日。range=5 中 VTI、VOO 各有 209 个正值与 52 个负值，QQQ 有 50 个正值与 211 个负值；三者均无空值或零值。range=3 的 QQQ 全部为负值。

| ETF | range=5 首个样本 | 持续不变起点 | 最后样本及连续长度 |
| --- | --- | --- | --- |
| VTI | 2021-10-18：6.29 | 2024-04-15：9.74 | 2026-10-12：9.74，连续 131 周完全相同 |
| VOO | 2021-10-18：4.35 | 2024-04-15：7.88 | 2026-10-12：7.88，连续 131 周完全相同 |
| QQQ | 2021-10-18：4.92 | 2024-04-15：-4.94 | 2026-10-12：-4.94，连续 131 周完全相同 |

range=10 的三个序列自 2024-05-01 至 2026-10-01 的连续 30 个点也分别保持上述相同数值。这与稳定提供最新估值的目标不符，表现上疑似停更或供应商补值，但没有把供应商内部原因写成已证实事实。不能因为时间戳延伸到 2026 年，就声称 2024 年后每周都有新的估值观察。

detail 端点会返回不同指标和窗口：本次 VTI 为 PE/range=3，VOO 与 QQQ 为 PB/range=5。因此不能不检查 `indicator` 就把 detail 数据当 PE。VTI overview 的日期为 2026-10-15，`circle=378.4200`、`part=38.8597`、`metric=9.74x`；这是响应所示值，不证明该分母于该日更新，更不证明它与公开页面旧的 `eps_ttm=25.659949` 是同一期间或口径。VOO、QQQ 的 PB 也分别有连续 131/132 周的末尾常量。

AAPL 的 range=1 PE 为 313 点（2025-10-16—2026-10-15），其中含 52 个周日；range=3/5 为周度，range=10 为月度且实际达到 120 点。因此同一接口会随窗口改变采样，连控制组的一年窗口都不能无条件当成交易日日度序列。

当前结论是**已取到三只 ETF 的历史 PE，但当前返回尚不适合接入本项目的指数 PE/EPS band**；不是“长桥没有 ETF 数据”，也不是账户权限缺失。研究数据独立导出至 `data/exports/longbridge-etf-pe-research-2026-10-16/`，未入生产数据库或替换已有来源。

导出共 9 份：每只 ETF 的 `request3y-weekly`、`request5y-weekly`、`request10y-monthly` 各一份。可从 [VTI 5 年请求周度 CSV](../data/exports/longbridge-etf-pe-research-2026-10-16/VTI-pe-request5y-weekly.csv)、[VOO 5 年请求周度 CSV](../data/exports/longbridge-etf-pe-research-2026-10-16/VOO-pe-request5y-weekly.csv)、[QQQ 5 年请求周度 CSV](../data/exports/longbridge-etf-pe-research-2026-10-16/QQQ-pe-request5y-weekly.csv)查看；文件名的请求窗口不代表实际覆盖长度。

## 长桥：最小权限授权与实际 scope 映射

后续用户再次要求深入推进长桥，已完成额外 **31 次**官方市场数据 GET，包含另一套历史估值入口、直接 EPS/净利润、最新快照及 ETF 资产配置，并逐证券核对原始年报/半年报。VTI/VOO 的 EPS 明确匹配截至 2023-06-30 的基金投资损益，QQQ 对应 2022-09-30；三只各自有原报证据，不再仅由 QQQ 推断。另在长桥分发的 Vanguard 官方事实表找到 2026-03-31 的 VTI PE 25.4 倍、VOO PE 26.1 倍；这些单点单独保留，不与会计 PE 拼接。当前详细结果见 [深入试取报告](longbridge-deep-probe-2026-10-16.md)、[盈利口径复算](longbridge-etf-earnings-basis-2026-10-16.md)及 [发行人文档渠道](longbridge-public-valuation-paths-2026-10-16.md)。以下保留此前授权过程。

[官方授权入口](https://open.longbridge.com/connect)和[Agent Auth Code 源文档](https://github.com/longbridge/developers/blob/main/docs/en/docs/agent-auth.mdx)支持用户登录后挑选权限，再生成一次性授权码。[CLI 文档](https://open.longbridge.com/docs/cli/install)说明 OAuth 设备流程和配置目录 `~/.longbridge/openapi/tokens/<client_id>`。

本次读取官网公开前端 `theme.BjkSWdOP.js` 与其 `use-mcp.BdmJsjzh.js`、`scope-terms.Cgs3dsu3.js`，追踪到公开权限目录：

```text
GET https://m-gl.lbkrs.com/api/forward/v1/mcp/scopes
x-platform: Web
app-id: longbridge
x-app-id: longbridge
x-bridge-token: none
accept-language: zh-CN
```

HTTP 200、`code=0`。公开元数据中的有效权限为：

| scope id | key | 实际能力 |
| --- | --- | --- |
| 4 | `watchlist` | 自选列表读取、创建、更新、删除；并非行情只读 |
| 6 | `account.read` | 账户资产、持仓、现金、流水；并非行情只读 |
| 10 | `trade.read` | 订单与成交查询等 |
| 11 | `trade.write` | 下单、撤单、定投交易等 |
| 12 | `sqkii` | 特定活动，前端隐藏；与本任务无关 |

官网当前文字把行情、报价、基本面归入随授权默认提供的“基础数据查询”，不属于上述可选权限。前端 `submitMCPAuthorization` 将空可选权限列表编码成 `scope="-1"`。因此**不能把 discovery 中的 4 或 6 猜作行情权限，也不要照抄旧示例中的 `scope=3`**。本次任务只需要基础数据查询，应取消所有可选的自选、账户、交易权限，并核验最终 token 的授权信息和只读接口能力。

第一轮长桥临时脚本在注册和设备授权请求中均显式提交 `scope=-1`；服务器接受设备请求，但响应未回显 scope。公开授权组件 `device-authorize-confirm.DV2NLfTS.js` 从 URL 只读取 `user_code`，然后提交用户所选 `mcpScopeId`，没有证据表明页面被请求参数锁定为基础权限。前两轮按当时的最小授权边界执行：用户取消全部可选项，收到令牌后再校验 scope；scope 缺失、未知或扩大时不运行任何业务查询。第三轮调整依据及结果另述。只读请求限定为上述两个估值端点的四个证券样本，令牌仅保存在进程内存，正式数据接入尚未发生。公开权限元数据、源码、试取脚本和脱敏摘要归档在被 Git 忽略的 `data/source-research/longbridge-probe-2026-10-16/`。

设备流程实测发现：用户完成授权后，服务端返回 `scope="watchlist account.read trade.read trade.write"`，JWT 的 `scopes` 也包含同样四项。即使客户端请求 `scope=-1`，本次结果仍超出只读基础行情范围。脚本拦截，未运行任何业务或账户请求，随后退出丢弃内存令牌；该旧授权的服务端 grant 未由脚本撤销，已告知用户在[官方授权管理页](https://open.longbridge.com/dashboard/authorized-apps)撤销临时客户端。此结果只证明本次授权范围不符合要求，不代表账户没有估值权限。

已切换到新的临时 public client，使用 OAuth 授权码、随机 state 和 S256 PKCE；官方 discovery 明确支持 S256，官网授权确认源码支持透传 challenge。新流程的授权首跳实际返回 HTTP 302，指向 `/oauth2/authorize/confirm`，保留 `scope=-1` 和 `code_challenge_method=S256`。本机回调接收授权码，不在聊天中传递；核验 response/JWT scope 后才能请求估值，结束或拒绝时调用官方 `/oauth2/revoke`，不持久化 token。首跳转发正确仍不等于最终授予的权限已正确，最终应以回调后的 scope 与实测结果为准。

PKCE 回调的实际结果仍为上述四项宽 scope，response 与 JWT 一致。第二轮同样阻止了全部 8 项估值查询，随后向官方撤销端点分别提交 refresh token、access token 的撤销请求，两个请求均获 HTTP 200；没有继续使用令牌验证撤销，也不把此结果宣称为所有历史服务端 grant 已删除。第二轮凭据随进程退出丢弃，没有写入文件或 Keychain。此问题尚不能归因于用户选项、前端或后端的具体一方；该阶段已确认的是请求/用户意图与最终 token 范围不一致，未继续使用该 token，也未在原有权限约束下重复同类授权。

用户随后明确允许使用带交易 scope 的授权，但要求实际只调用数据读取接口。基于这项新的授权，第三轮仍记录真实 scope，改由业务请求硬允许列表限制行为：仅 GET `/v1/quote/valuation`、`/v1/quote/valuation/detail`，仅 AAPL.US/VTI.US/VOO.US/QQQ.US，`indicator=pe`，range 仅 1/3/5/10；不添加账户、持仓、订单接口。第三轮实际只返回 `watchlist`，并完成前述 20 次 GET。令牌仅保留在进程内存，研究完成后主动 stop 并撤销。前两轮证据保留，未将新授权回溯解释成前两轮已经获准。

另有两个值得保留的源码核查结果：

- [CLI `src/auth.rs`](https://github.com/longbridge/longbridge-terminal/blob/main/src/auth.rs) 的 device login 会自动调用系统浏览器，不是只打印 URL。本项目禁止未经明确授权自动使用 Chrome，应使用仅打印链接的受控回调，或让用户自行打开官方网页。
- [CLI `src/cli/auth.rs`](https://github.com/longbridge/longbridge-terminal/blob/main/src/cli/auth.rs) 的 `cmd_auth_status` 会初始化客户端并读取账户资料，包含持仓兜底路径，与文档“纯本机”描述不完全一致。最小权限核验不应依赖这一命令；可检查凭据存在性并直接请求所需行情端点，输出需脱敏。

AP（香港/新加坡）与 US 账户属于不同数据中心；US 账户只能使用 `.com` 接入点，`.cn` 不支持 US 数据中心。网络接入点和账户数据中心不要混为一谈，见[快速开始](https://open.longbridge.com/docs/getting-started)。[通用 FAQ](https://open.longbridge.com/docs/qa/general)称 OpenAPI 不收额外接口访问费用，高级行情仍受账户已有行情产品限制；未找到对 `valuation_history` 单独资产门槛或 ETF 覆盖的官方保证，需要实际调用。

替代认证路径也未被视为已验证的只读方案。[快速开始](https://open.longbridge.com/docs/getting-started)说明可在开发者用户中心获取传统 App Key/App Secret/Access Token，同时明确说明该凭据可用于账户交易；未找到单独只读开关的一手证据。[中文 FAQ](https://open.longbridge.cn/zh-CN/docs/qa/general)说明可启用模拟账户，其行情权限与实盘相同，交易权限随证券账户而异。模拟凭据可作为后续隔离实盘交易的候选，但不等于只读 token，也未证明其 ETF 历史估值可用。本轮未执行此路径。

## 老虎：日度历史 PE/EPS 接口与额外权限

[官方 Python SDK 源码](https://github.com/tigerfintech/openapi-python-sdk/blob/master/tigeropen/quote/quote_client.py)提供：

```python
quote_client.get_stock_fundamental(symbols=["VTI", "VOO", "QQQ", "AAPL"], market="US")
quote_client.get_financial_daily(
    symbols=["VTI", "VOO", "QQQ", "AAPL"],
    market="US",
    fields=["p_to_ltm_diluted_eps_before_extra"],
    begin_date="2026-09-01",
    end_date="2026-10-15",
    timezone="US/Eastern",
)
```

历史方法返回 `symbol`、`field`、毫秒时间戳 `date`、`value`。本轮读取的官方 SDK 3.8.0 估值枚举采用 `p_to_ltm_diluted_eps_before_extra`；[官方 SDK 配套说明](https://github.com/tigerfintech/tigeropen-skill/blob/main/skills/tigeropen/references/quote.md)另列 `pe_ttm`、`eps_ttm`，但这两个短名不在正式枚举中，不能推荐为已验证字段。当前快照采用 `ttm_pe_rate`、`ttm_eps`，EPS 报告字段另见利润表枚举。上述专用服务尚未成功取得数据；对 AAPL、VTI 改用正式日估值字段后仍返回同一财务权限错误，不能将失败仅归因于旧字段名。

[官方 FAQ](https://docs.itigerup.com/docs/faq)明确 `get_financial_report`、`get_stock_fundamental`、`get_financial_daily` 默认未开放，需要额外申请基本面数据权限。已有账户、已购买行情、成功 OAuth 都不能替代此权限。网页可能可见不表示 API 已授权。

随后对用户账户的只读实测确认了这项限制。按官方 MAC 格式设置设备标识后，`get_quote_permission` 返回 HTTP 200、`code=0`、`data=[]`，且响应 RSA 签名验证通过；不能将此空列表称为已取得所有行情权限。VTI 与 AAPL 控制组的 `stock_fundamental`、`financial_daily` 均返回 HTTP 200、`code=4`，消息为 `1010:biz param error(you don't have permission to access financial service in US market)`。这是供应商明确拒绝美国市场基本面服务，不是 ETF 空数据，也不是 HTTP 请求失败。初轮 VOO/QQQ 返回相同错误，当时规范设备标识后的复核以 VTI/AAPL 为准，该阶段尚未取得 API PE/EPS。首次脱敏响应保留在 `data/source-research/tiger-probe-2026-10-16/sdk-device-check/`；本轮继续推进又复测四只证券，专用服务仍拒绝，另经 K 线取得了以下真实历史 PE。详情见[账户权限及 K 线实测记录](tiger-account-probe-2026-10-16.md)。

官方 [`get_bars` 日 K 文档](https://docs.itigerup.com/docs/quote-stock)提供 `with_fundamental` 参数，原始响应字段为 `ttmPe`、`lyrPe`。使用 `period=day`、`right=nr`，按纽约时间戳分段请求及重叠复核后，VTI、VOO、QQQ 各取得 1,029 个非零 PE，覆盖 2022-09-09—2026-10-15，按本机 NYSE 日历在此区间无缺日。其中 VTI、VOO 各 241 个负值，QQQ 有 251 个负值，均忠实保留；未将当前 PE 填入旧日期。历史窗口末条可能缺 PE，2022-12-30 是由另一真实重叠响应补入，其他非空重叠值未发现冲突。早于该起点没有取得 PE，也未验证全历史的财报可得时点或修订情况。

研究 CSV、来源路径和覆盖摘要独立保存在 [Tiger 历史数据导出目录说明](../data/exports/tiger-etf-pe-research-2026-10-16/README.md)。其中 EPS 是 `close/ttmPe` 算出的隐含分母，不是接口直接提供的 EPS。近期 33 个 ETF 收盘价与此前 moomoo 归档逐日相同，只说明近期价格得到对照，不验证 PE，也不意味着两个渠道拥有独立上游。

官方公开网页本轮抓取的可见值（页面抓取时间与行情时点可能不同，以下不是统一时点的横向比价）：

| 页面 | 页面价格 | PE | EPS(TTM) | PB |
| --- | --- | --- | --- | --- |
| [VTI](https://www.itiger.com/stock/VTI) | 381.57 | 9.14 | 41.76 | 0.31 |
| [VOO](https://www.itiger.com/stock/VOO) | 709.43 | 7.87 | 90.15 | 0.63 |
| [QQQ](https://www.itiger.com/stock/QQQ) | 728.00 | 6.50 | 112.06 | 1.29 |

公开 HTML 中 VTI `ttmEps` 精确值为 41.758587。以上页面的 Valuation Analysis 栏未返回历史估值值。初次公开网页只能发现数值与发行人投资组合 PE 不同；后续已用 API 样本和发行人原始报表取得更具体的证据：

- QQQ 近期 `ttmPe` / `lyrPe` 高度匹配 2025 财年基金运营损益 `71,901,131,802 / 641,650,000` 份所构成的分母；该损益包括基金已实现及未实现投资收益。11 个近期交易日重算 PE 与 API 差异均小于 0.000001。见 [QQQ 专项核验](tiger-etf-pe-basis-2026-10-16.md)。
- VTI、VOO 当前 `ttmPe` 均与 2024 财年全基金运营损益除以所有份额类别期末数量之和对应，`lyrPe` 则对应 2025 财年。2026-10-15 四项 PE 均匹配到六位小数，11 日扩展核验的 44 项比较全部相差不超过 0.000001。份额类别之间 NAV 不同，机械合计也不等于统一 ETF 单位。见 [VTI / VOO 专项核验及 SEC 原报](tiger-vti-voo-pe-basis-2026-10-16.md)。

这是强数值对应证据，尚非 Tiger 对内部字段算法的确认；但已没有把这些值直接当作持仓公司盈利估值的依据。研究数据保留，不接入生产 band。继续推进应向官方确认是否另有持仓穿透 PE/EPS 产品，而非假设开通同源财务权限即可解决口径问题；[待发送的支持说明及申请路径](tiger-fundamental-permission-path-2026-10-16.md)已整理。

## 老虎：账号开通与只读测试入口

[官方简介](https://docs.itigerup.com/docs/intro)要求个人完成开户与入金后到[开发者个人中心](https://developer.itigerup.com/profile)开通 API；[准备工作](https://docs.itigerup.com/docs/prepare)说明需要 API 协议和开发者信息。OpenAPI 实时行情与 App/PC 权限独立；历史行情额度与基本面权限又是不同维度，不应为查 PE 直接购买无关的 L2 行情。

[认证文档](https://docs.itigerup.com/docs/authentication)明确个人可用 OAuth2（Python SDK/CLI `tigeropen >= 3.8.0`），可只授权行情，避免交出继承全部权限的 RSA 私钥。一次授权最长维持 15 天；持续每日任务需要评估续授权成本。OAuth2 不替代已有 OpenAPI/基本面权限。

[CLI 文档](https://docs.itigerup.com/docs/cli)的配置目录为 `~/.tigeropen/`，支持 `tigeropen --auth oauth2 config init`、`tigeropen auth login/status`；SDK 旧签名配置文件为 `tiger_openapi_config.properties`，香港 TBHK 牌照另有 `tiger_openapi_token.properties`。[准备工作](https://docs.itigerup.com/docs/prepare)指出后者默认 30 天有效，需主动配置续期。不要输出这些文件内容。

后续只读验证顺序：先确认登录/API 授权，再获取行情权限，随后以 AAPL 为控制组同时请求 VTI、VOO、QQQ 的当前与短区间历史基本面。分别记录鉴权失败、额外基本面权限缺失、ETF 返回空、非空但含义未核实，不能用其中一个结论替代另外几个。老虎初始化 `QuoteClient` 默认抢占行情设备权限；进行纯权限探测应考虑 `is_grab_permission=False`，避免影响用户其他行情客户端。
