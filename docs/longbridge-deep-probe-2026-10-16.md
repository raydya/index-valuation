# 长桥深入试取：历史接口、盈利期间与发行人事实表

核验日期：2026-10-16。用户要求继续推进长桥；使用用户完成的 OAuth 授权，执行 31 次只读市场数据 GET，覆盖 VTI、VOO、QQQ，并用 AAPL 作普通公司对照。全部请求 HTTP 200、业务 code 0。未修改生产数据源、数据库、网页、调度或依赖。

## 当前结论

**长桥现有估值和财报接口没有提供适合本项目的 ETF 持仓公司盈利历史。** 本轮取得了更多真实数据，也定位到其财务期间和基金会计口径，不能仅通过修复权限或更换字段解决。同时找到一个有用的独立渠道：长桥分发的 Vanguard 官方事实表包含 VTI、VOO 的有日期组合 PE 单点；它尚不构成连续历史数据源。

| 能力 | 本轮实际结果 | 对 band 的意义 |
| --- | --- | --- |
| 独立估值对比接口 | 各 ETF 60 个按月标签，其中仅 2024-01—09 的 9 个 PE 非空，且全部为负；当前 PE 原值为 0 | 历史 9 点可由 2022 年度基金投资损益 EPS 复算，不能当作持仓公司盈利估值；0 不作为有效 PE |
| 直接 EPS / 净利润 | VTI EPS 25.659949、VOO 58.050577、QQQ -88.498502；接口净利润与原始基金运营损益逐美元相同 | VTI/VOO 为截至 2023-06-30 的滚动一年，QQQ 为截至 2022-09-30 的财年；不是当前指数盈利 |
| 最新财报快照 | VTI/VOO 截至 2023-06-30，QQQ 截至 2022-09-30；AAPL 则截至 2026-06-27 | 问题并非整个账户没有基本面权限；行情更新日期不可当作财报有效日 |
| 年度 EPS 报表 | 每只 ETF 各 7 年，2016—2022；AAPL 有截至 2025 年的年报 | 真实取得历史 EPS，但仍是基金会计口径且 ETF 财报停在旧期间 |
| 原估值图复测 | 1 年请求均为空，5 年请求各 261 个周度点，末尾 131 周仍完全恒定 | 排除了此前一次请求失败或本地解析出错；不能作为新估值观测使用 |
| ETF 资产配置 | VTI/VOO 为 2026-08-31，QQQ 为 2026-10-14；各有前十大持仓 | 有现代持仓数据，但没有完整持仓、历史权重或组合 PE，不能仅用 Top 10 重算全基金估值 |
| 发行人事实表 | 2026-03-31：VTI 及其基准 25.4 倍，VOO 及其基准 26.1 倍 | 可保留为单独的发行人组合 PE 参考；没有连续历史，PDF 未完整说明 TTM/预期、加权及亏损处理 |

会计输入、原始 SEC 文档和可复算结果见 [盈利口径专项核验](longbridge-etf-earnings-basis-2026-10-16.md)。事实表下载入口、有效日和来源分层见 [公开文档渠道](longbridge-public-valuation-paths-2026-10-16.md)。

## 授权与实际调用

上一轮研究凭据已撤销，本机没有长桥环境变量或 SDK token 缓存，因此本轮复用公开 client ID，重新由用户在官方页面完成 PKCE S256 授权。本机回调校验 state，未自动打开浏览器；令牌只在进程内存中使用，不写入项目或输出。实际 scope 为 `watchlist`，响应与 JWT 一致；按用户此前明确许可接受实际 scope，同时业务调用严格限定市场数据 GET，没有读取或修改自选列表、账户、资产、持仓或交易。

| 路径 | 本轮参数 | 次数 |
| --- | --- | ---: |
| `/v1/quote/compare/valuation` | 各证券 `symbol`、`currency=USD` | 4 |
| `/v1/quote/financials/earnings-snapshot` | 各证券 `symbol`，由服务端返回最新财报 | 4 |
| `/v1/quote/financial-reports` | 各证券 `symbol`、`kind=IS`、`report=af` | 4 |
| `/v1/quote/etf-asset-allocation` | 三只 ETF 的 `symbol` | 3 |
| `/v1/quote/valuation` | 四证券分别显式 `indicator=pe&range=1/5`，另各一次仅传 symbol 复核 SDK 默认行为 | 12 |
| `/v1/quote/valuation/detail` | 四证券的 `symbol` | 4 |
| **合计** | **6 个路径，均为市场数据读取** | **31** |

所有响应保存在 `data/source-research/longbridge-deep-probe-2026-10-16/`，含请求业务参数、观测时间和原始返回，不含认证请求头或令牌。结束时撤销 refresh/access token 的请求均返回 HTTP 200，本机进程退出；没有继续使用已撤销令牌。

官方入口依据为 [估值对比](https://open.longbridge.com/docs/fundamental/fundamental/valuation-comparison)、[财报](https://open.longbridge.com/docs/fundamental/fundamental/financial-report)、[财报快照](https://open.longbridge.com/docs/fundamental/fundamental/financial-report-snapshot)及 [SDK 的 FundamentalContext](https://github.com/longbridge/openapi/blob/4cfc453e36ea9936f6a04c647830127e1218716a/rust/src/fundamental/context.rs)。

## 数据结构与口径验证

- 比较接口保留实际 `counter_id=ETF/US/<代码>`、`symbol` 和 USD 币种；历史日期由 Unix 秒按纽约时区转换，保留月初标签，不擅自移到交易日。原始空串、负值、零值分别保留，未生成日度 PE。
- 年报返回顶层 `symbol` 为空，使用 `IS.indicators[].entry.symbol` 核对身份；快照返回 `ticker` 与请求相同。不因顶层空值直接接受错误证券，也不把空字段填成请求代码后声称来源已返回。
- 新比较接口的历史与当前 EPS 不是同一口径。三只 ETF 共 27 条历史 PE 可用 2022 年度静态基金 EPS 重建对应价格，当前 VTI/VOO EPS 却对应截至 2023-06-30 的滚动基金损益。旧估值图的 131 周常量与这一月度序列也不能拼接。
- 新财报接口的 `net_income` 与发行人原报运营损益一致，包含基金投资的已实现及未实现涨跌。Vanguard 两只基金的分母还合计了不同 NAV 的份额类别；该数值用于追溯来源，不作为正确 ETF 份额盈利算法。
- ETF 持仓 Top 10 权重分别为 VTI 33.45134%、VOO 37.80884%、QQQ 46.93437%。分类合计接近 100% 不表示已经取得每一只持仓；不能以分类权重补造完整证券清单。

公开源码还发现 SDK 5.2.1 新增 HTTP 报价传输，可查询当前静态 EPS 与 PE。本轮未对这些 POST 路径作认证实测；它们没有历史持仓估值参数，不将其描述为已解决历史问题。另有财报明细和 US 数据中心专用接口，但没有证据证明其提供另一套 ETF 持仓盈利历史。范围及精确参数见 [SDK 路径复查](longbridge-sdk-valuation-routes-2026-10-16.md)。

## 研究导出与复现

导出目录为 [研究数据说明](../data/exports/longbridge-deep-research-2026-10-16/README.md)：

- 三份 `*-compare-history.csv` 各 60 行，保留 51 个空 PE 与 9 个负 PE。
- 三份 `*-annual-fund-eps.csv` 各 7 行，忠实记录 2016—2022 年度基金 EPS，不标成持仓盈利。
- `report-snapshots.csv` 包含三只 ETF 和 AAPL 的财报日期对照。
- `top10-holdings.csv` 包含 30 条持仓；`summary.json` 保存覆盖、原始文件 SHA-256、证券身份验证与恒定序列统计。

使用项目环境运行以下命令可离线重建导出，不发网络请求、不连接数据库：

```bash
.venv/bin/python data/source-research/longbridge-deep-probe-2026-10-16/export_research.py
.venv/bin/python data/source-research/longbridge-etf-basis-2026-10-16/reproduce_basis.py
```

发行人事实表另作独立来源归档，不能与上述会计 EPS 或冻结估值图合并。研究文件均为本机忽略数据目录；文档可进入版本管理，凭据和原始研究数据不随 Git 提交。

## 后续应确认的具体产品

本轮公开页面说明，长桥估值图的财务数据由 S&P Global 提供，ETF 配置/持仓则注明 Morningstar。这是页面对不同模块的归属说明，不能将任意 ETF EPS 都称为 Morningstar 的持仓估值。长桥分发的 Vanguard 事实表又是发行人文档，与上述两类 API 序列分别保存。

继续推进应向长桥确认是否开放 **Morningstar ETF 组合特征中的持仓 PE、历史日期和完整口径**，或是否能提供发行人历史事实表列表。仅询问“有无 EPS”会再次得到基金会计字段；仅修复旧财报更新，也不会把基金投资损益变成持仓公司盈利。官方支持入口和可直接使用的文案已记录在 [公开文档渠道](longbridge-public-valuation-paths-2026-10-16.md)，本轮没有代用户联系供应商或购买数据。
