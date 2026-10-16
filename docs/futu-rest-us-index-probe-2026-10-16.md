# 富途 REST 美股指数接口实测

核验日期：2026-10-16（北京时间）。此次为只读接口验证，没有切换指数配置、写入估值数据库或修改网页。A 股和港股的既有 OpenD 流程继续使用原配置。

本文件保留切换前的试验结果；用户随后已授权正式接入和历史回填，当前配置及覆盖见 [REST 接入记录](futu-rest-integration-2026-10-16.md)。下文临时授权和一年试取限制不代表后续持续授权的运行状态。

## 结论

**富途 REST 可以返回标普500、纳斯达克100的指数价格和历史指数 PE。** 同日复测的 OpenD 仍对这两个指数返回 `US stock indices are not supported`。因此，OpenD 的限制不能扩大为富途所有接口均不支持美股指数，也没有证据表明购买 OpenD 行情卡可以解决。

用户完成 `quote:read` 授权后，REST 基本资料确认 `US..SPX` 是 S&P 500 Index、`US..NDX` 是 NASDAQ 100 Index，均为 `stock_type=IDX`。代码中是两个连续的点，不应自行改成 `US.SPX` 或 `US.NDX`。

| 标的 | 日线试取范围 / 条数 | 估值详情 PE 范围 / 条数 | 2026-10-15 收盘点位 | 同日 PE | 点位 / PE |
| --- | --- | --- | ---: | ---: | ---: |
| 标普500 | 2026-10-08—2026-10-15 / 6 | 2025-10-17—2026-10-15 / 250 | 7744.06 | 25.858 | 299.4841055 |
| 纳斯达克100 | 2026-10-08—2026-10-15 / 6 | 2025-10-17—2026-10-15 / 250 | 29935.5947 | 32.447 | 922.5997689 |

两条 PE 序列均有250个有效正值，日期没有重复，返回起止日之间没有 NYSE 日历交易日缺口，也没有日历外日期。时间戳转换为纽约日期与 `time_str` 全部一致。这只证明本次请求的一年窗口；**没有试取更长窗口、历史分页或证明自指数发布日起完整覆盖**。

SPY、QQQ、VOO、VTI 各返回6条价格，但 K 线 `pe_ratio` 全部为0，不能作为历史 ETF PE。普通股票 `US.FUTU` 对照在相同窗口的6条 K 线均有非零 PE。两个指数的 K 线 PE 也全部为0，指数历史 PE 必须从独立的估值详情接口取得。

VTI 对应的美国全市场指数尚未在此 REST 通道确认身份及覆盖，不能将 VTI 价格当成指数点位。

## 试取接口

REST Host 为 `https://webapi.futunn.com`，无需本地 OpenD。

1. `POST /api/v1.0/quote/stock-basicinfo`，JSON 参数 `code_list`，确认证券代码、名称和 `stock_type`。部分无效代码可能被跳过，须逐一核对返回列表。[官方文档](https://open.futunn.com/zh-cn/api/quote/basic-data/stock-basicinfo)
2. `GET /api/v1.0/quote/{symbol}/history-kline`，本次使用 `start=2026-10-08`、`end=2026-10-15`、`ktype=2`（日线）、`autype=0`（不复权）、`num=10`。[官方文档](https://open.futunn.com/zh-cn/api/quote/basic-data/history-kline)
3. `GET /api/v1.0/quote/{symbol}/valuation/detail?valuation_type=1&interval_type=3`，分别选择 PE 和一年窗口，读取 `trend.historical_items` 的 `time_str`、`time`、`value`。`ret_code=0` 之外仍须验证返回值和日期。[官方文档](https://open.futunn.com/zh-cn/api/quote/valuation/detail)

估值文档另有5年、10年、20年、30年窗口参数，但本次没有实测其覆盖和采样，不能将枚举值作为可用历史年数。文档将专用估值接口品类限定为正股、指数，不承诺 ETF。

日线实测 `time_zone=-4`，与文档“分钟偏移”的描述不一致；`time_key` 对应纽约交易日零点，也不是收盘瞬间。后续适配应使用 `America/New_York`、明确交易日及市场收盘规则，不能直接把这些字段当作收盘时间或按分钟解释偏移。

## 交叉核验与口径边界

只读查询项目数据库中已存的同日价格进行比对，没有重新采集已经停用的数据源。

- 标普500的6个交易日与 FRED 分发的标普官方价格完全一致；与 Yahoo 的最大相对差异约0.0000031%。
- 纳斯达克100的6个交易日均有 Yahoo 对照，重合的 Nasdaq/FRED 数据也参加核验；全部比较的最大相对差异约0.0000148%。2026-10-15 当时数据库尚无 Nasdaq/FRED 同日值，该日仅与 Yahoo 比较。
- 以上证明价格核验通过，不等于 PE 财务口径已经独立验证。不同渠道也可能共享上游。
- REST 文档只把指数字段标为 PE，没有明确指数盈利期间、GAAP/调整后盈利、权重、聚合公式及亏损成分处理。富途[公司估值说明](https://support.futunn.com/topic497?from_platform=1&lang=zh-cn)中的 TTM 定义不能直接外推为指数定义。
- 本次取得的是“富途指数 PE”，不是已确认由 S&P/Nasdaq 编制机构提供的官方 PE。上表商数使用 `Decimal` 计算，只称“基于富途 PE 的隐含 EPS”。
- Yahoo ETF 代理与富途指数 PE 是不同输入，不能直接以同口径5%阈值判定其正确性，也不能把新样本拼接进既有代理方法。

## 授权与运行边界

官方 [REST 快速开始](https://open.futunn.com/zh-cn/api/overview/getting-started)支持 OAuth 2.1 + PKCE。实测可读取的 discovery 为：

- `https://mcp.futunn.com/.well-known/oauth-authorization-server`
- `https://mcp.futunn.com/.well-known/oauth-protected-resource/mcp`

未授权的 SPX/NDX 日线请求均返回 HTTP 401、`code=-12006`，这属于认证阶段结果，不能解释为指数行情权限不足。完成用户登录授权后，相同通道成功返回数据；此次没有购买行情卡。

临时公共客户端只请求 `quote:read`；服务端注册响应还附带账户范围限定 `accid:*`。试取进程检查令牌能力仅为 `quote:read`，未请求账户或交易接口。令牌只保留在进程内，未写入项目或日志，回调不记录授权码。

试取结束后进程及本地回调已关闭。按 discovery 公布地址请求撤销令牌得到 HTTP 404，因此不声称服务端撤销成功；本地没有保存可继续使用的令牌。尚未验证持续授权、令牌刷新和定时采集，不能把本轮试取等同于长期自动化已接通。

## 原始证据

- `data/source-research/futu-rest-access-probe-2026-10-16.json`：无认证访问、服务时间及 discovery。
- `data/source-research/futu-rest-authenticated-probe-2026-10-16.json`：授权后的基本资料、7只证券日线、两个指数和普通股票对照的 PE 响应；不含认证请求头或令牌。
- `data/source-research/futu-opend-us-index-recheck-2026-10-16.json`：同日 OpenD 两指数的价格/估值不支持响应。
- `data/source-research/futu-rest-validation-2026-10-16.json`：日期、交易日历、价格逐条对照、隐含 EPS 及原始归档路径。
- `data/raw/futu_rest_access_probe/`、`data/raw/futu_rest_probe/`：按内容哈希保存的公开接口响应和市场数据。

后续接入应先验证更长历史与可持续认证，再使用独立的来源/方法版本接入通用估值流程，保留既有代理历史。此记录不构成切换生产来源的操作。
