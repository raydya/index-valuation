# VTI 的其他指数代理候选

核验日期：2026-10-16。范围为官方定义、代表性及估值口径的方案研究，并对富途 REST 的两个候选作了只读小样本试取；本次没有更改生产指数或请求新的账户授权。

结论：可以用其他美国全市场指数判断 VTI 所代表市场的估值环境。**立即可用**的是项目已接入的 **S&P 500** band，作为大盘估值参考，不能改名为 VTI 全市场估值。**更贴近全市场的长期方案**是优先验证 **Russell 3000** 和 **S&P Total Market Index** 的真实历史指数 PE。下表的研究优先级并非已获数据的可用性排序。

## VTI 当前基准身份

Vanguard 当前产品页写明 VTI 跟踪 **Morningstar US Total Market Index**，覆盖约100%的美国可投资股票市场，含大、中、小、微盘。2026-04-29 官方公告说明，此次源于 CRSP 指数更名为 Morningstar，相关基金于2026-07-29改名，投资目标及管理方式不变。因此不能误称为 VTI 在2026年改投了一个无关指数。[VTI 官方产品页](https://advisors.vanguard.com/investments/products/vti/vanguard-total-stock-market-etf?source=autosugg)、[更名公告](https://corporate.vanguard.com/content/corporatesite/us/en/corp/who-we-are/pressroom/press-release-vanguard-to-update-names-of-us-equity-index-funds-tracking-morningstar-indexes-042926.html)。

## 优先级

| 候选 | 与 VTI 基准的关系 | 代表 ETF | 当前应如何使用 |
| --- | --- | --- | --- |
| Russell 3000 | 大、中、小盘约3000家公司，约98%美国可投资股票市值；比全市场少一部分最小公司 | IWV | 优先核验历史指数 PE；已有官方当前指数 PE 及明确亏损处理标签 |
| S&P Total Market Index（TMI） | 大、中、小、微盘，浮动市值加权，无盈利准入门槛；从覆盖设计看很接近全市场 | ITOT | 与 Russell 3000 并行候选；要验证指数自身历史 PE，不能由 ITOT 能报价推断指数估值可得 |
| S&P 500 | 约80%可用市值，以大盘为主；有盈利准入条件，缺少完整中小微盘 | VOO、SPY 等 | 项目已有 band，可立即作为美股大盘估值参照；不替换 VTI 身份 |

Russell 3000 的规模覆盖、2026年开始半年度重构见 [LSEG 官方定义](https://www.lseg.com/en/ftse-russell/indices/russell-3000-index)，无盈利筛选见 [LSEG 官方说明](https://www.lseg.com/content/dam/ftse-russell/en_us/documents/other/russell-your-index-matters-factsheet.pdf)，IWV 基准映射见 [iShares 产品页](https://www.ishares.com/uk/professionals/en/products/239714/ishares-russell-3000-etf?siteEntryPassthrough=true&switchLocale=y)。

S&P TMI 的覆盖及 ITOT 映射见 [指数产品页](https://www.spglobal.com/spdji/en/indices/equity/sp-total-market-index-tmi/)；2026年7月版方法说明第4、9、12页分别说明浮动市值加权、TMI无盈利门槛、成分构建。S&P 1500 体系的新纳入标准要求最近季度及最近连续四季度累计 GAAP 持续经营净利润为正，有 REIT 及成分迁移等例外，不能将其规则套到 TMI。[S&P 美国指数方法](https://www.spglobal.com/spdji/en/documents/methodologies/methodology-sp-us-indices.pdf)。S&P 500 的约80%覆盖见 [官方产品页](https://www.spglobal.com/spdji/en/indices/equity/sp-500/)。

上述优先级是针对本项目的研究判断，不是已经证实三者 PE 可互换；本轮未计算历史回报相关性或同日 PE 偏差。

## 已找到的指数估值证据

Russell 3000 官方事实表截至2026-09-30列示 **P/E Ex-Neg Earnings = 23.93**、成分数量2996；Russell 3000E 对应23.92及3432。该字段明确排除负盈利，与包含亏损公司盈利的整体市盈率可能不同。它证明官方发布指数估值快照，**不能据此声称已取得连续历史 PE 或日更新 API**。[官方事实表](https://research.ftserussell.com/Analytics/FactSheets/Home/DownloadSingleIssue?isManual=True&issueName=US3000USD&openfile=open)。

选择替代指数的实际完成条件仍是：能够取得足够长、真实日期、同一口径的指数价格和历史 PE，能每日或按原始频率更新；并核实亏损处理、盈利期间及点时性。定义更接近 VTI 并不能单独解决数据源问题。

## 本次富途 REST 小样本验证

通过5次只读调用核对目录、估值详情和2026-10-01至2026-10-15日线：

| 返回证券 | 身份结果 | 短期价格 | PE结果 | 本次能否组成band |
| --- | --- | --- | --- | --- |
| `US..W5000FLT` | Wilshire 5000，类型IDX | 0条 | 请求成功但无趋势或历史记录 | 不能 |
| `US..DJUS` | Dow Jones U.S.，类型IDX；不是Total Stock Market | 11条 | 请求成功但无趋势或历史记录 | 不能 |

两只估值请求均使用 `valuation_type=1`、`interval=10`，返回HTTP200、业务code0，但仅有空分布对象及估值类型，不能把成功响应当作取得PE，也不能把空数据当0。以上是当前账户与本次区间的实测，不推断供应商永远不支持。`US..W5000FLT` 也不能未经代码与版本核对就当作现代 FT Wilshire 5000；理论候选的官方覆盖定义不能替代代码身份核验。

原始响应目录：[本次试取](../data/source-research/vti-alternative-index-probe-2026-10-16/)。这些响应没有改变生产配置或数据库。实际结果没有提供能立即取代 VTI 的新全市场 band，现成参考仍为项目已有 S&P 500。

## 其他候选为何暂不排在前面

- **Dow Jones U.S. Total Stock Market Index**：包含所有符合条件的美国普通股、浮动市值加权，也是合理全市场代理；本轮未验证历史 PE 能力。注意 **SCHB 实际跟踪的是 Dow Jones U.S. Broad Stock Market Index**，约2500家大中小公司，不能把 Broad 与 Total 两个名称当作同一指数。[Total 官方定义](https://www.spglobal.com/spdji/en/indices/equity/dow-jones-us-total-stock-market-index/)、[方法说明](https://www.spglobal.com/spdji/en/documents/methodologies/methodology-dj-us-total-stock-market-indices.pdf)、[SCHB 官方产品页](https://www.schwabassetmanagement.com/products/schb)。
- **FT Wilshire 5000**：同样是广泛全市场系列。官网列明有全市值及浮动市值版本，落地需具体确认版本，不能只凭“5000”名称映射；本轮未验证可持续历史 PE，因此没有证据把它优先于上述两个候选。[官方定义](https://www.wilshireindexes.com/products/ft-wilshire-5000-index-series)。
- **S&P Composite 1500**：由500、400、600组成，官方称约90%美国市值，ETF为SPTM。比标普500更广，但存在盈利准入条件、微盘覆盖不足，从全市场代表性看不如 TMI。[官方定义](https://www.spglobal.com/spdji/en/indices/equity/sp-composite-1500/)。

## 项目中应保持的边界

建议新增独立的“美国全市场估值代理”指数页，保留真实指数名称、代码、来源与方法，使用 **同一代理指数的同日价格 ÷ PE** 得到该指数的隐含 EPS，再生成它自己的 band。VTI 页面可链接或并排展示该参考。

不应将 Russell 3000 或 TMI 的 PE 直接配上 VTI 价格，并标为 VTI 的真实 EPS/band；那是混合标的的模型估算。各指数都按市值加权，并不代表成分、盈利、亏损处理或 PE 历史分位相同。若用户以后明确要求这种模型，必须独立命名、量化历史偏差，且不能替代官方或直接来源记录。

新增指数必须另行完成项目的来源实测、历史回填、独立方法和网页验证。本轮仅回答候选与口径可行性，现有 `ustotal` 暂停状态保持不变。
