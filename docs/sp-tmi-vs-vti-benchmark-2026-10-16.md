# S&P Total Market 与 VTI 基准的比较

核验日期：2026-10-16。仅研究公开方法与发行人资料；未改生产来源、指数配置或数据库。

## 结论及身份

S&P Total Market Index（TMI）与 VTI 当前基准 Morningstar US Total Market Index 都是美国全市场、自由流通市值加权指数。两者适合比较美国整体股票市场，但不能合并为同一条指数或同一口径 PE 历史。

VTI 基准原名 CRSP US Total Market。2026 年更名为 Morningstar 不代表改投另一套指数；Vanguard 说明基金名称于 2026-07-29 更新，投资目标及管理方式不变。[Vanguard 更名公告](https://corporate.vanguard.com/content/corporatesite/us/en/corp/who-we-are/pressroom/press-release-vanguard-to-update-names-of-us-equity-index-funds-tracking-morningstar-indexes-042926.html)。

## 编制规则

以下依据 [S&P U.S. Indices Methodology，2026年7月](https://www.spglobal.com/spdji/en/documents/methodologies/methodology-sp-us-indices.pdf) 及 [Morningstar Market Indexes Methodology Guide，2026年7月](https://indexes.morningstar.com/api/docs/6a64c1efd7ce7b357809b99e)。完整出处与细则见同目录两份 `research-*-rules-2026-10-16.md` 笔记。

| 比较项 | S&P TMI | VTI 当前基准／原 CRSP |
| --- | --- | --- |
| 市场范围 | 美国合资格大、中、小、微盘股票；成分数量不固定 | 美国合资格大、中、小、微盘股票；约100%可投资市场；成分数量不固定 |
| 加权 | 自由流通市值 | 自由流通市值；流通因子有分档取整机制 |
| 盈利门槛 | 无，不适用 S&P1500 的盈利准入规则 | 无正盈利准入筛选 |
| 新纳入最低总市值 | 无最低要求，但仍需符合其他资格 | 1500万美元；既有成分总市值低于1000万美元时剔除 |
| 新纳入流通比例 | 通常至少10%；满足大型流通市值条件可替代 | 通常至少12.5%；快速 IPO 为10%；满足大型流通市值条件可替代 |
| 流动性 | 新成分年成交金额／流通市值至少0.1；既有成分无该最低要求 | 过去125交易日平均日成交量／流通股至少0.001；既有成分连续两次季度评审低于0.0008剔除 |
| 重构与调整 | 每年9月全面重构；季度纳入IPO、转板等资格变化，并维护股份及流通权重 | 3、6、9、12月季度重构；5交易日过渡，通常每天完成约20%的持仓变更 |
| IPO | 普通IPO下一次季度再平衡评估；流通市值至少20亿美元的大型IPO可快速纳入 | 普通新股通常需20交易日交易历史；达到规模及其他资格者可在第5交易日收盘后快速纳入 |

流通比例例外不能忽略：S&P 的替代条件为证券自由流通市值至少达到 TMI 第100大公司总市值的10%；Morningstar 为至少达到合资格市场总流通市值的0.5个基点。两个流动性比率分子、时间窗口不同，不能直接把0.1与0.001比较为宽严倍数。

由规则可推断，差异主要体现在边缘小微盘股票、流通比例、IPO 纳入时间及权重调整；不能据此给出未经同日全量持仓计算的重合率，也不能把 ETF 持仓只数当作指数成分数。

## 实际基准回报对照

使用两家发行人截至2026-06-30的资料内列出的历年**基准总回报**，不是ETF成交价涨跌。VTI 的 Spliced Total Stock Market Index 在这些年份已全部使用 CRSP；ITOT 在这些年份已全部使用 S&P TMI。

| 年份 | S&P TMI | VTI 基准／CRSP | S&P减CRSP，百分点 |
| --- | ---: | ---: | ---: |
| 2021 | 25.66% | 25.72% | -0.06 |
| 2022 | -19.53% | -19.49% | -0.04 |
| 2023 | 26.06% | 25.98% | +0.08 |
| 2024 | 23.87% | 23.77% | +0.10 |
| 2025 | 17.05% | 17.15% | -0.10 |

按披露值计算，五个年度的绝对差均不超过0.10个百分点。该观察支持两者市场表现接近，不证明逐日相关系数、持仓完全一致、未来跟踪误差或 PE 可直接互换。

来源：[ITOT factsheet，p.1](https://www.ishares.com/us/literature/fact-sheet/itot-ishares-core-s-p-total-u-s-stock-market-etf-fund-fact-sheet-en-us.pdf)、[VTI investment profile，p.2–3](https://institutional.vanguard.com/assets/corp/fund_communications/pdf_publish/us-products/investment-profiles/0970.pdf)。

## 跟踪产品与历史边界

美股直接对应产品为 **ITOT — iShares Core S&P Total U.S. Stock Market ETF**，BlackRock/iShares 发行、NYSE Arca 上市、费用率0.03%、2004-01-20成立。[当前产品页](https://www.ishares.com/us/products/239724/ishares-core-sp-total-us-stock-market-etf)。

ITOT 在 **2015-12-18** 从 S&P Composite 1500 改为 S&P TMI。2004年基金成立不等于当年已经跟踪 TMI；更早基金历史、拼接基准与纯 TMI 历史需分别标记。[BlackRock 换基准公告](https://ir.blackrock.com/news-and-events/press-releases/press-releases-details/2015/BlackRock-Announces-Changes-to-iShares-Core-for-Buy-and-Hold-Investors/default.aspx)。

加拿大上市的 **XUU** 跟踪 S&P Total Market Index 的加元计价版本；官方同页说明另有加元对冲版 **XUH**。它们涉及币种或汇率对冲，不能直接替代美元价格回报序列。[XUU 官方产品页](https://www.blackrock.com/ca/investors/en/products/272104/ishares-core-sp-us-total-market-index-etf)。本清单不是所有国家上市产品的穷尽列表。

## 对估值系统的含义

可以把 S&P TMI／ITOT 作为 VTI 的美国全市场估值参考候选，但需独立名称、证券身份、回报版本和方法。尚未验证一套可持续更新且可完整回填的该指数或ETF历史 PE。

ITOT 官方网页与事实表确实披露持仓 P/E。其事实表 p.3 定义使用过去12个月盈利，排除负盈利及非常项目，单股 PE 超过60按60处理。因而该字段不应直接称为 VTI 官方 PE，点位除以该 PE 得到的也只能是对应口径的代理隐含 EPS；找到 ITOT 不等于已解决历史 PE 数据可得性。
