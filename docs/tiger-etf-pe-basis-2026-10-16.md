# 老虎 QQQ PE 分母核验：匹配基金投资损益

核验日期：2026-10-16。只读研究，未写入生产估值。VTI、VOO 的逐证券核验见 [独立记录](tiger-vti-voo-pe-basis-2026-10-16.md)。

**老虎 `kline` 确实返回 QQQ 的历史 `ttmPe`、`lyrPe`，但本次最近 11 个交易日的分母与 QQQ 2025 财年基金自身投资损益精确吻合到 PE 返回精度。不能将这些数据直接当作 Nasdaq-100 成分公司盈利的估值 band 输入。**

## API 观测及匹配方法

已授权行情试取的原始文件：

`data/source-research/tiger-probe-2026-10-16/market-followup-20261016T104802Z/rsa-QQQ-kline-with-fundamental.json`

请求 `method=kline`、`period=day`、`right=nr`、`with_fundamental=true`，返回成功，并通过 TLS 与 SDK 对应的 RSA 响应签名校验。该文件包含 2026-10-01 至 2026-10-15 共 11 个交易日。签名验证证明响应来自对应服务，不证明财务字段满足本项目口径。

例如 2026-10-15 的原始日线收盘价为 **728.00 美元**，`ttmPe=6.496715`、`lyrPe=6.496715`。由价格除以 PE 反推的数为 **112.0566316977…**。这是本研究的反算值，不能写成该响应直接返回了 EPS。

## 发行人财报的复算证据

[Invesco 官方 QQQ 2025 年报](https://www.invesco.com/content/dam/invesco/hk/en/pdf/annual-report/Invesco_QQQ_AnnualReport.pdf)的纸面第 7–8 页列出截至 **2025-09-30** 财年的以下数据：

| 字段 | 美元或份额 |
| --- | ---: |
| 基金净投资收入 | 1,779,222,203 |
| 基金已实现净收益 | 55,060,201,495 |
| 持有证券未实现升值变动 | 15,061,708,104 |
| 运营导致的基金净资产增加 | 71,901,131,802 |
| 财年末基金份额 | 641,650,000 |

用 `Decimal` 可复算：

```python
from decimal import Decimal, getcontext

getcontext().prec = 40
fund_result = Decimal("1779222203") + Decimal("55060201495") + Decimal("15061708104")
fund_result_per_share = fund_result / Decimal("641650000")
# 112.0566224608431387828255279357905400140

reconstructed_pe = Decimal("728.00") / fund_result_per_share
# 6.496715535526612794277972331048119263931
```

采用这一固定分母，逐一重算全部 11 天的 `close / fund_result_per_share`：

- 与 `ttmPe` 的最大绝对差为 **0.00000083343**。
- 与 `lyrPe` 的最大绝对差为 **0.00000060766**。
- 11 天两组结果均小于 PE 六位小数末位的 1 个单位，即 **0.000001**；这不是仅以“PE 看起来太低”判断。

并非每一天都与普通四舍五入完全相同，故不声称已还原老虎全部中间精度及舍入算法。这是强力的原始会计数据匹配证据，仍不等同于老虎对供应商字段血缘的直接确认。

该分子包含基金投资组合的已实现及未实现价格收益，分母是期末基金份额；它既不是成分公司净利润的加权汇总，也不是单独的股息收入。年报第 9 页另列每份投资运营结果 **115.15**，采用不同每份核算方法，不能与本次“总运营损益 / 期末份额”的 **112.056622…** 混同。

本次观测发生在 2026 年 10 月，匹配到的会计期间却是 2025 年 9 月结束的财年；API 的 `ttmPe` 标签不能独自证明分母已滚动至当前最近十二个月。未据此推断其他历史日期都采用同一财年或算法。

## 对项目的处理

可以把老虎接口视为**已证实返回历史 PE 字段的渠道**，但 QQQ 当前这一字段不适合直接用于本项目的成分公司盈利估值。生产 Nasdaq-100 继续采用既有富途 REST 指数方法；本研究数据仅存放在隔离的研究目录。

老虎其他财务接口仍须分别检查实际权限和字段。`financial_daily` 的官方 PE 字段为 `p_to_ltm_diluted_eps_before_extra`，与本页 K 线的 `ttmPe` 不是可自动互换的同名接口。VTI、VOO 的结论须依据各自报表核验，不能从 QQQ 推广。

## 本机可复核产物

目录 `data/source-research/tiger-etf-basis-2026-10-16/`：

- `QQQ-annual-2025-09-30.pdf`：此次下载的 Invesco 官方报告；SHA-256 为 `b666864cec748d29153a20b52fb579a25870e3e4f08515ec9ffd715af415ad1c`。
- `source-manifest.json`：公开原文 URL、采集时间与哈希。
- `QQQ-fy2025-operations-match.csv`：11 天逐条反算、API 原值和差异。
- `QQQ-fy2025-operations-match-summary.json`：原始财报数字、精度和核验摘要。

此前 Longbridge QQQ 负 EPS 与 2022 年基金投资损益的匹配属于另一来源、另一时期，见 [原始核验](etf-accounting-eps-validation-2026-10-16.md)；本页没有把该旧值当成老虎当前 EPS。
