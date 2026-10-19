# 老虎账户只读实测：K 线取得 ETF 历史 PE，专用基本面仍受限

首次核验：2026-10-16 17:28—17:29；后续推进：同日 18:46—18:52（Asia/Shanghai）。本次只研究来源能力，没有改变生产指数配置、数据库、富途采集规则或依赖。

**当前结论：RSA 认证可用；官方 `kline` 启用 `with_fundamental=true` 后，VTI、VOO、QQQ 均已取得 2022-09-09 至 2026-10-15 的 1,029 条非零 TTM PE，按本地 NYSE 日历检查该区间没有 PE 缺日。专用 `stock_fundamental`、`financial_daily` 仍明确拒绝访问，但不能因此宣称所有老虎接口都拿不到 PE。** 三只 ETF 的当前样本已与基金自身运营损益强匹配，不适合直接充当持仓公司盈利的指数 band 输入。研究 CSV 已保存，未写入生产估值表。该会计匹配有原始报表证据，但不是 Tiger 对内部算法的正式确认，也未逐条验证全历史算法。

以下前两节保留首次试取事实；最新覆盖和新增请求见“后续推进”一节。ETF 盈利口径分别见 [QQQ 核验](tiger-etf-pe-basis-2026-10-16.md)、[VTI / VOO 核验](tiger-vti-voo-pe-basis-2026-10-16.md)。

## 首次认证与请求范围

按用户指定，仅从项目根目录 `tiger_openapi_config.properties` 在进程内读取 RSA 凭据。没有输出或复制私钥、Tiger ID、账户号，也没有将这些值写入研究记录。根目录配置及可选 token 文件已加入 `.gitignore`；配置文件权限为 `600`，当时没有 token 文件。

先前的 quote-only OAuth 设备流程未取得 access token，收到使用本地配置的指示后已停止，进程内设备授权状态随进程退出释放；没有保存 OAuth token。此次实际结果来自 RSA 认证。

使用项目已有 `httpx`、`Crypto`，参照官方 `tigeropen 3.8.0` 的 JSON 参数序列化、RSA PKCS#1 v1.5 / SHA1 签名和返回验签规则，向 `https://openapi.tigerfintech.com/gateway` 发出受白名单约束的请求。没有安装新依赖，没有初始化会自动抢占行情设备访问权的 `QuoteClient`，也没有调用 `grab_quote_permission`。

仅使用三个方法：

| 方法 | 查询对象与内容 |
| --- | --- |
| `get_quote_permission` | 当前设备行情权限列表，不查询账户资产 |
| `stock_fundamental` | VTI、VOO、QQQ、AAPL 当前基本面；官方 SDK 展示 `ttm_pe_rate`、`ttm_eps` 等字段 |
| `financial_daily` | 同样四只证券，`market=US`，当时按官方参考 skill 请求 `pe_ttm`、`eps_ttm`；这两个字段未取得有效返回，后续已发现与 SDK 枚举不一致，不继续作为已验证字段推荐 |

日财务请求的纽约时间边界为 `2026-10-08 00:00:00` 至 `2026-10-16 00:00:00`，对应毫秒参数 `1791432000000`、`1792123200000`。这是**请求区间**；没有返回任何数据日期，不能称为已获取该区间的历史。

未调用账户、资产、持仓、订单或交易接口，未购买行情，未代用户联系客服。

## 首次实际响应与设备参数复核

首轮使用临时 UUID 作为设备标识，行情权限查询返回 `get device information error`。复核官方 SDK 后，改为 SDK 使用的本机 MAC 格式，且仍不抢占设备权限，再请求一次权限列表及 VTI/AAPL 的当前、历史基本面。两轮证据分别保留，没有用第一次设备错误推断账户能力。

| 请求 | HTTP / 业务结果 | 能确认的事实 |
| --- | --- | --- |
| 按官方设备标识查询 `get_quote_permission` | HTTP 200，`code=0`，`message=success`，`data=[]`；官方返回 RSA 签名验证通过 | RSA 认证、网关和公共参数工作正常；此次当前设备返回空权限列表。没有抢占权限，未进一步测试实时行情，不推断 APP 行情权益 |
| VTI、VOO、QQQ、AAPL 的 `stock_fundamental` | 全部 HTTP 200，业务 `code=4`，`data=null` | 美股基本面服务拒绝访问，当前 PE/EPS 未取得 |
| 同四只证券的 `financial_daily` | 全部 HTTP 200，业务 `code=4`，`data=null` | 历史 PE/EPS 未取得 |
| 修正设备标识后的 VTI、AAPL 复核 | 当前和历史接口仍是相同拒绝 | 该拒绝不是首次设备格式错误造成；AAPL 控制证券同样失败，不能归因于 ETF 类型 |

基本面接口返回的完整错误为：

```text
1010:biz param error(you don't have permission to access financial service in US market)
```

基本面错误响应未带可验证的 `sign`，记录中 `sdk_rsa_response_verification=false`；HTTPS 证书验证始终开启。权限查询成功响应带签名并通过验签。这两种证据强度分别保存，没有把无签名错误响应写成已做应用层验签。

首次共执行 14 次小范围行情/权限请求：首轮 9 次，设备格式复核 5 次。当时计划的 2011 年短区间日财务探测因权限阻塞未发出。这是首次结论；同日晚间通过 K 线接口扩展历史的结果如下。

## 后续推进：专用财务拒绝与 K 线 PE 成功并存

此次继续推进先新增 25 次受控只读请求，再用 4 次边界请求复核，共 29 次，分四批保留：

| 批次及目录（均在 `data/source-research/tiger-probe-2026-10-16/`） | 请求数 | 结果 |
| --- | --- | --- |
| `followup-20261016T104629Z/` | 9 | 行情权限查询 code 0、空列表且验签通过；四证券的当前基本面和日财务共 8 请求仍为 1010 US financial service 权限拒绝 |
| `market-followup-20261016T104802Z/` | 6 | 权限查询成功；四证券 `kline(with_fundamental=true)` 均成功，分别返回 2026-10-01—15 的 11 条每日 `ttmPe`、`lyrPe`；另一次 `stock_detail` 报 4000 当前设备没有权限 |
| `history-followup-20261016T104956Z/` | 10 | 权限查询成功；7 次分段批量 K 线请求取得三只 ETF 的可返回历史；VTI、AAPL 用 SDK 正式 PE 枚举字段请求日财务仍为 1010 |
| `boundary-followup-20261016T105219Z/` | 4 | 权限查询及 3 次短窗口批量 K 线均成功；补得真实 2022-12-30 PE，核实历史窗口末条 PE 缺失现象，以及 2021 年抽样仍无 PE |

所有成功的行情权限/K 线响应都记录 RSA 验签通过，HTTPS 验证开启；业务错误响应无可验证签名，保持相应标记为 false。没有抢占设备行情权，没有请求账户资产、持仓、订单或交易，也没有购买产品或联系客服。

### 字段与日期复核

SDK 的日估值枚举实际提供 `p_to_ltm_diluted_eps_before_extra`，并未列出旧参考 skill 中的 `pe_ttm`、`eps_ttm`。后续对 VTI、AAPL 显式请求该正式枚举字段，仍得到同一个 US financial service 权限拒绝，确认不是仅换一个未经验证的字段名就能解决。成功 K 线原始字段为 `ttmPe`、`lyrPe`，独立于专用日财务返回结构。[官方字段源码](https://github.com/tigerfintech/openapi-python-sdk/blob/master/tigeropen/common/consts/fundamental_fields.py)

K 线请求使用 `period=day`、`right=nr`，按纽约时区边界分为 7 个不重叠区间：1999—2003、2003—2007、2007—2011、2011—2015、2015—2019、2019—2023、2023-01-01—2026-10-16；每次上限 1,200 条，每只证券实际单段行数均未达到上限。结束日期不包含 2026-10-16。逐条保留返回日期，不用当前 EPS 向过去填值。

近期三个 ETF 的未复权收盘价与此前 moomoo 同期已归档数据逐日比较：2026-10-01—15 共 33/33 个同日价格完全一致，日期没有遗漏或重复。原始毫秒时间戳在纽约均为当地零点。此核验只覆盖近期价格；moomoo PE 为零，不构成独立 PE 核验。详细过程见[权限路径与响应审核](tiger-fundamental-permission-path-2026-10-16.md)。

### 长历史覆盖与边界复核

以下为本轮最终统计：PE 已合并 18:52 短窗口取得的真实观测，价格行数复核后仍为 6,370 / 4,050 / 6,944，短窗口只补入缺失 PE，没有增加价格行。存在的历史价格缺日及日历外记录仍如实保留，因此不能称为从发行日起无缺口。

| ETF | 价格行数 | 实际价格日期 | 非零 TTM PE 行数 | 实际 PE 日期 | 正 / 负 PE |
| --- | --- | --- | --- | --- | --- |
| VTI | 6,370 | 2001-05-31—2026-10-15 | 1,029 | 2022-09-09—2026-10-15 | 788 / 241 |
| VOO | 4,050 | 2010-09-09—2026-10-15 | 1,029 | 2022-09-09—2026-10-15 | 788 / 241 |
| QQQ | 6,944 | 1999-03-10—2026-10-15 | 1,029 | 2022-09-09—2026-10-15 | 778 / 251 |

首轮三个 ETF 都没有返回 2022-12-30 的 PE。改请求 `2022-12-28—2023-01-05` 后，该日真实返回 VTI `-8.520963`、VOO `-9.488458`、QQQ `-3.008864`；但该短窗末条 2023-01-04 没有 PE，而先前长窗该日有值。另一短窗 `2022-09-01—2022-09-17` 末条 2022-09-16 也缺 PE，先前长窗有值。证据表明历史请求窗口末条可能丢失 PE，不宜把所有单窗空值立即归为供应商永久缺口。

合并采用同日真实非空观测，既补入 2022-12-30，也保留其他响应中已经存在的 2023-01-04 和 2022-09-16；没有前向填充，没有用短窗口空值覆盖已存非空值。最终该 PE 首尾区间内按本地 NYSE 日历没有缺日。2022-09-09 之前仍未取得 PE，2021-10-01—15 的独立短窗全部无 PE，不把首尾区间连续等同于基金成立以来完整。

最终价格按 NYSE 交易日历检查，VTI 有 12 个价格缺日，QQQ 有 1 个缺日及 1 条日历外日期（2005-02-21），VOO 未发现价格缺日。完整日期列表保存在研究 `summary.json`；原始异常不删除、不挪日期，不直接进入生产计算。

`close/ttmPe` 使用 Decimal 计算，是研究反算值而非接口直接返回的 EPS；保留正、负 PE 原值以及来源响应路径，不把负 PE 当作零、抹去亏损期，或只用正值拼接历史。

### 会计分母已取得实质匹配证据

2026-10-15 的 VTI `ttmPe=9.062088` 对应 2024 财年全基金运营损益 `346,153,203,000 / 8,289,389,590`，所得分母约 `41.7585877997`；VOO `ttmPe=7.869659` 对应 2024 财年 `254,533,400,000 / 2,823,521,698`，分母约 `90.1474921125`。同日 VTI `lyrPe=10.049568`、VOO `lyrPe=7.139532` 则对应各自 2025 财年，四项复算均匹配到接口六位小数。因此不能根据 `ttmPe` 名字把分母当作截至 2026 年最新十二个月盈利。[VTI / VOO 证据及逐日精度](tiger-vti-voo-pe-basis-2026-10-16.md)

QQQ 同日 `ttmPe=6.496715` 对应截至 2025-09-30 财年的运营损益 `71,901,131,802 / 641,650,000`，分母约 `112.0566224608`；近期 11 日重建 PE 与接口之差均小于 `0.000001`。[QQQ 证据](tiger-etf-pe-basis-2026-10-16.md)

上述损益包含基金已实现及未实现投资价格收益；VTI / VOO 分母还机械合计不同净值单位的份额类别。它们与持仓公司净利润的加权估值含义不同，故此处不是简单“看起来 PE 太低”或仅等待一般口径说明，而是已有明确证据支持不直接接入生产 band。仍未获得 Tiger 的算法、数据血缘、舍入规则及全历史一致性确认。

### 研究数据导出

目录：`data/exports/tiger-etf-pe-research-2026-10-16/`。

- `VTI-price-and-pe.csv`、`VOO-price-and-pe.csv`、`QQQ-price-and-pe.csv`：所有实际返回的价格及其可用 PE。
- `VTI-pe-only.csv`、`VOO-pe-only.csv`、`QQQ-pe-only.csv`：真实有 PE 的观测，不补齐缺口。
- `summary.json`：首尾日期、行数、正负值、日历对比、原始文件 SHA-256 和最新行来源。

这些文件均为 `research_only`，不代表已完成应用接入。`indices.toml`、生产数据库、网页 band 与日常富途采集均保持既有设置；美国全市场暂停状态没有解除。

## 下一步与申请文案

老虎官方 FAQ 明确 `get_financial_daily`、`get_stock_fundamental`、`get_financial_report` 等需要额外基本面权限，默认不开通，需要联系客服申请。该限制不否定本轮成功的 K 线 PE，购买美股实时行情也不等于取得专用基本面权限。[官方 FAQ](https://docs.itigerup.com/docs/faq)

用户可通过自己的老虎客服渠道发送以下说明；这段文字尚未发送：

> 我已开通 Tiger OpenAPI，RSA 认证可用。`stock_fundamental` / `financial_daily` 报 US financial service 权限错误，VTI/AAPL 使用正式字段 `p_to_ltm_diluted_eps_before_extra` 也一样；但 `kline(with_fundamental=true)` 已取得三只 ETF 自 2022-09-09 起的 PE。
>
> 我需要的是持仓公司盈利的穿透估值，不是基金投资损益。2026-10-15：VTI close=378.42、ttmPe=9.062088，可由 2024 年基金运营损益 346153203000 / 全类别份额 8289389590 重建；VOO close=709.43、ttmPe=7.869659，可由 2024 年 254533400000 / 2823521698 重建。两只基金 lyrPe 则对应 2025 年。QQQ close=728、ttmPe=6.496715，与 2025 财年基金运营损益 71901131802 / 份额 641650000 匹配到返回精度。
>
> 请确认这些字段的实际算法及数据血缘，并重点告知是否另有持仓公司穿透 PE/EPS 或指数估值产品，支持哪些历史日期、权重和亏损处理。若专用 US financial service 仍提供同源基金会计数据，仅开通权限不能满足此需求。请先确认产品适用性、资格和费用；需要的仅是数据只读权限，不需要交易权限，不直接开通收费服务。

申请时无需提供私钥。应先确认是否另有满足持仓穿透需求的产品，而不是假设开通现有基金会计财务就能生成指数 band。若确认覆盖并开通，再用短区间和 AAPL 对照核验，再扩展日财务/报告覆盖。已取得 K 线可继续研究，但当前会计匹配证据不支持将其作为 CRSP 等指数官方 PE/EPS。官方入口及费用条件见[权限申请路径](tiger-fundamental-permission-path-2026-10-16.md)。

## 证据位置与官方依据

研究脚本与原始行情响应仅保存在已忽略的本机目录，不随 Git 提交：

- `data/source-research/tiger-probe-2026-10-16/rsa_quote_probe.py`：受控只读请求脚本；当前版本已使用官方 MAC 设备标识。
- `data/source-research/tiger-probe-2026-10-16/rsa-*-stock-fundamental.json`、`rsa-*-financial-daily.json`：首轮四证券响应，含请求业务参数、时间和返回原值，不含认证请求参数。
- `data/source-research/tiger-probe-2026-10-16/sdk-device-check/rsa-quote-permissions.json`：修正设备格式后的成功权限响应。
- `data/source-research/tiger-probe-2026-10-16/sdk-device-check/rsa-VTI-*.json`、`rsa-AAPL-*.json`：复核响应。
- 两级目录各自的 `rsa-probe-summary.json`：请求摘要。错误消息为真实结果，不把 HTTP 200 或脚本退出 0 当成取得数据。
- 后续四批目录及内容见上表；`history-followup-20261016T104956Z/rsa-{VTI,AAPL}-canonical-financial-daily.json` 保留正式 SDK 字段仍被拒绝的证据，`boundary-followup-20261016T105219Z/` 保留用重叠窗口取得缺失 PE 的真实返回。

参考一手资料：

- [老虎认证方式](https://docs.itigerup.com/docs/authentication)：签名和 OAuth2 两种方式。
- [老虎官方 FAQ](https://docs.itigerup.com/docs/faq)：基本面额外权限、行情设备访问权与 OAuth 授权期限。
- [官方 Python SDK 3.8.0 发布包](https://pypi.org/project/tigeropen/3.8.0/)：本次核对 `tiger_open_client.py`、`tiger_open_config.py`、`common/util/signature_utils.py`、`fundamental/request/model.py`、`quote/quote_client.py`；仅公开下载并在内存读取源码，没有安装到产品环境。
- [老虎官方行情接口参考](https://github.com/tigerfintech/tigeropen-skill/blob/main/skills/tigeropen/references/quote.md)：保留首次字段选择依据，但其中日财务示例与 SDK 枚举有差异，不作为短字段已经被服务端支持的证明。
- [官方 QuoteClient 源码](https://github.com/tigerfintech/openapi-python-sdk/blob/master/tigeropen/quote/quote_client.py)：K 线 `with_fundamental` 参数及基本面接口签名。
