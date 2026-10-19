# ETF EPS 口径核验：QQQ 数值与基金自身投资损益精确匹配

核验日期：2026-10-16。此次仅查发行人原始报表及已有公开网页记录，没有请求用户的券商接口、读取凭据或修改生产数据。

**Longbridge 公开页面的 QQQ `eps_ttm=-88.498502`，可精确还原为 QQQ 2022 财年基金自身投资损益除以期末基金份额。这个数值不能作为当前 Nasdaq-100 成分公司穿透盈利的证据。VTI 的两组券商 EPS 尚未完成同样的还原，不跨证券推断。**

## 被核验的数值

以下来自本轮已保存的公开页面研究，采集时间不等于 EPS 对应财务期间，页面值与 API 权限、API 历史覆盖也不是同一问题。详见[券商研究记录](broker-etf-valuation-research-2026-10-16.md)。

| 来源 | 证券与字段 | 值 | 本次结论 |
| --- | --- | --- | --- |
| [Longbridge QQQ 页面](https://longbridge.com/en/quote/QQQ.US) | `eps_ttm` | -88.498502 | 与下述 2022 基金会计数据的商匹配到全部六位小数 |
| [Longbridge VTI 页面](https://longbridge.com/en/quote/VTI.US) | `eps_ttm` | 25.659949 | 未证实其财务来源及基金/持仓口径 |
| 同上 | `eps` / `eps_forecast` | -32.690164 / 45.006538 | 不因字段标签就假定属于当前成分公司盈利 |
| [Tiger VTI 页面](https://www.itiger.com/stock/VTI) | `ttmEps` | 41.758587 | 未完成原始报表精确还原；页面 9.14 是 PE，不是 EPS |

Longbridge 公开 HTML 已由券商研究任务归档于 `data/source-research/longbridge-probe-2026-10-16/QQQ.US_public_quote.html`、`VTI.US_public_quote.html`，观测时间及字段见同目录 `public_quote_eps_summary.json`。随后已授权 API 的 VTI 详情返回不同分母 38.8597；**不能把本文核验的公开网页旧 EPS 当作该 API 当前分母**。API 的数值、历史频率和停更现象另见券商研究记录，本次报表匹配结论仅覆盖明确列出的公开数值。

## QQQ：原始报表与可复现计算

原始来源为 Invesco 向 SEC 提交的[2023-01-27 生效招募说明书及所含 QQQ 财务报表](https://www.sec.gov/Archives/edgar/data/1067839/000119312523017732/d365144d485bpos.htm)。报表中 2022 年栏对应**截至 2022-09-30 的完整财年**。

| 原始字段 | 原文纸面页码 | 美元 / 份额 |
| --- | --- | ---: |
| 基金净投资收入 `Net investment income` | 32 | 1,078,880,692 |
| 基金已实现净收益 `Net realized gain` | 32 | 21,590,609,931 |
| 投资证券未实现升值/贬值变动 | 32 | -71,618,012,262 |
| 基金运营导致的净资产变动 | 32；33 重列 | -48,948,521,639 |
| 2022-09-30 期末基金份额 | 31；33 重列 | 553,100,000 |

分子是前三项之和，包含基金持有证券的已实现与未实现价格变动。分母是该基金的**期末份额**，不是成分公司的总股本，也不是期间加权平均份额。原始财务报表没有将这个商标为成分公司 EPS。[SEC 原始报表](https://www.sec.gov/Archives/edgar/data/1067839/000119312523017732/d365144d485bpos.htm)

使用 `Decimal` 复现：

```python
from decimal import Decimal, getcontext

getcontext().prec = 40
investment_income = Decimal("1078880692")
realized_gain = Decimal("21590609931")
unrealized_change = Decimal("-71618012262")
period_end_fund_shares = Decimal("553100000")

fund_operations = investment_income + realized_gain + unrealized_change
per_share = fund_operations / period_end_fund_shares

assert fund_operations == Decimal("-48948521639")
assert per_share.quantize(Decimal("0.000001")) == Decimal("-88.498502")
print(per_share)
# -88.49850233050081359609473874525402278069
```

这不是仅凭负值、与发行人 PE 不同或“大致接近”作判断，而是两个原始财务字段精确复算至网页显示精度。**这是强力的会计来源匹配证据，但仍不是 Longbridge 对供应商字段血缘的直接确认。** 不应把该结论扩展为 Longbridge 所有证券、所有 EPS 字段都采用同一算法。

同份报表 Financial Highlights 的 2022 每份投资运营结果是 -88.59，与上述商不同；净投资收益每份 2.01 的脚注采用期间平均份额。没有把 Financial Highlights 的每份结果和“总运营损益 / 期末份额”混成同一指标。

## VTI：最新报表显示的口径边界，仍未还原券商值

仅下载并检查了 Vanguard 最新[2026-06-30 财务报表 NCSR6.pdf](https://personal1.vanguard.com/funds/ncsr/NCSR6.pdf)，没有广泛抓取历年大报告。Total Stock Market 基金章节纸面页 55–59（合并 PDF 的约 201–205 页）分别列出全基金报表与各份额类别。

- 2026 上半年，全基金运营导致的净资产增加为 **229,757,137,000 美元**。
- 六种份额类别合计 **8,228,463,868 份**，其中 ETF 类别 **1,792,590,857 份**；各类别 NAV 不同，不能把它们当成同一每份计量单位。
- ETF 类别的 2026 上半年每份净投资收益为 **1.975**，每份已实现及未实现收益为 **34.937**，每份投资运营结果合计 **36.912**；2025 全年合计为 **49.317**。这些都是基金层面的财务指标。[Vanguard 原始报表](https://personal1.vanguard.com/funds/ncsr/NCSR6.pdf)

作为排查，`229757137000 / 8228463868 = 27.922239…`，没有匹配 Longbridge 的 25.659949；直接把全基金损益除以 ETF 类别份额会得到 128.170427…，分子与分母范围不一致，不能将该商称为 VTI EPS。**此次未找到 25.659949、41.758587 或 -32.690164 的确切财务期间和分母，不能因此把这些值定性为错误或已证实的基金资本利得。**

已下载的公开原始报告和文本位于本机忽略目录：

- `data/source-research/etf-accounting-validation-2026-10-16/NCSR6-2026-06-30.pdf`
- `data/source-research/etf-accounting-validation-2026-10-16/NCSR6-2026-06-30.txt`

## 对本项目的影响

以股票价格波动产生的基金已实现/未实现收益作为 band 的盈利分母，会把基金价格回报混入盈利估值。QQQ 这个精确匹配的例子已经足以证明：**券商为 ETF 返回名为 EPS 的字段，并不自动满足“持仓公司穿透盈利”的要求。**

对新来源的启用仍需逐证券确认字段定义、财务期间、份额口径和历史采样日期。QQQ 本例不进入指数盈利 band；VTI 保持口径未确认状态，等待供应商定义或可复算的原始数据，不能仅因接口可返回数值就启用生产估值。
