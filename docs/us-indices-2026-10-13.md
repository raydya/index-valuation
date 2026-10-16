# 美股指数接入记录

采集日期：2026-10-13（北京时间）。美国最近已收盘日：2026-10-12。

本文保留初次接入时的结果。2026-10-15起，标普500、纳指100已按用户指定切换至 Yahoo SPY/QQQ ETF 代理 PE，官方价格优先，停用东方财富及旧 PE 来源的新增采集；当前规则见 [代理接入记录](yahoo-etf-proxy-2026-10-15.md)。下文旧主源、覆盖、调度时刻不能作为当前运行配置。

2026-10-16起，美国全市场也已接入 Yahoo VTI 代理 PE，并以 Google Finance 日线核验 Yahoo 指数价格，详见 [VTI 接入记录](vti-proxy-2026-10-16.md)。下文“价格专用”描述仅代表首次接入时的状态。

2026-10-16后续已按用户指定将标普500、纳指100切换至富途 REST 价格与指数 PE，停止 Yahoo 新增采集；美国全市场因无已验证 REST 映射暂停，旧历史只读。**当前运行规则以 [REST 接入记录](futu-rest-integration-2026-10-16.md)为准**。切换前的只读探针另见 [REST 实测记录](futu-rest-us-index-probe-2026-10-16.md)。

## ETF 与指数身份

| ETF | 对应指数 | 系统标识 | 价格系列 |
| --- | --- | --- | --- |
| VOO | S&P 500 | `sp500` / `US..SPX` | Yahoo `^GSPC`，东方财富 `100.SPX` |
| QQQ | Nasdaq-100 | `nasdaq100` / `US..NDX` | FRED `NASDAQ100`、Nasdaq `NDX`、Yahoo `^NDX`、东方财富 `100.NDX100` |
| VTI | Morningstar US Total Market，原 CRSP US Total Market | `ustotal` / `US.CRSPTM1` | Yahoo `^CRSPTM1` |

确认依据：[Vanguard VOO](https://advisors.vanguard.com/investments/products/voo/vanguard-sp-500-etf)、[Invesco QQQ](https://www.invesco.com/qqq-etf/en/about.html)、[Vanguard VTI](https://advisors.vanguard.com/investments/products/vti/vanguard-total-stock-market-etf)。QQQ 对应纳斯达克100，不是纳斯达克综合指数；东方财富的 `100.NDX` 是综合指数，不能用来核验 QQQ 基准。

Morningstar 于2026-07-28完成原 CRSP 指数品牌更名，基准连续性不因名称变化而重建。[Morningstar 更名公告](https://indexes.morningstar.com/insights/article/blt572a569013072dee/new-name-same-trusted-benchmarks-introducing-the-morningstar-market-indexes)。`CRSPTM1` 为价格回报版本，`CRSPTMT` 为全收益版本；本项目不把二者拼接。[CME 基准代码说明](https://www.cmegroup.com/articles/faqs/faq-e-mini-equity-index-futures-expansion.html)

OpenD 的美股指数目录含 `US..SPX`、`US..NDX`，但日线和估值接口均返回 `US stock indices are not supported`；目录没有 VTI 基准。`US.CRSPTM1` 是本项目内部身份，不声称为富途可调用代码。用户已授权美股指数采用其他 PE 主源、独立标注口径并交叉核验。

## 实际入库覆盖

| 指数 | 日度价格 | PE 采样 | 可计算隐含 EPS |
| --- | --- | --- | --- |
| 标普500 | 17,520条，1957-03-04—2026-10-12 | 470条，2016-10-12—2026-10-12 | 469条，最近2026-10-08 |
| 纳斯达克100 | 10,337条，1985-10-01—2026-10-12 | 469条，2016-10-12—2026-10-12 | 468条，最近2026-10-08 |
| 美国全市场 | 3,906条，2011-03-31—2026-10-12 | 暂无可靠指数 PE | 暂无 |

PE 采样稀疏，以上数量不是每日完整估值。零值、非交易日以及尚未收盘的日期不生成派生值。供应商历史可能经过修订，不是财报披露时点数据库。

### 价格核验

- 标普500：Yahoo 从正式发布日开始；东方财富从1986-03-17开始，10,075个重合交易日差异均不超过1%。早期7,445个日度点位只有单源，保留 `unverified`。
- 纳指100：FRED 主价格始于1986-01-02；更早可用 Yahoo 数据始于1985-10-01，尚未覆盖1985-01-31发布日至9月末。Nasdaq 公共历史接口的早期记录出现重复旧值和差异，均保留原始响应及异常状态；没有根据其他来源悄悄改写原值。FRED 与 Yahoo 有10,231日差异≤1%、41日为1%—5%。FRED 重发 Nasdaq 的数据，属于另一分发渠道，不是独立编制的指数。[FRED 来源说明](https://fred.stlouisfed.org/series/NASDAQ100)
- 美国全市场：目前只有 Yahoo 日度价格，因此没有通过价格交叉核验。首次全历史请求的2026-10-12日线 close 为 null；之后最近14天的增量请求返回了该日有效 close，现已正常补入。没有采用概览报价或前日价格填空。官方基准的历史回溯始于2001年，当前接口未提供该早期价格。2011-03-31为回测末日，正式实时阶段始于4月1日。
- Yahoo 必须使用明确的 `period1/period2` 和 `interval=1d`；`range=max` 会被上游降采样为月线或季线，适配器拒收这类响应。保存 `close`，不将 ETF 复权价或总回报指数代入点位。
- 美国日历使用锁定版本的 `pandas-market-calendars` NYSE 规则，归档包版本、开收盘时间及日期；包含9·11、Sandy等特殊停市测试。它是本地日历规则，并非实时交易所公告源。时区 `America/New_York` 自动处理夏令时，当日保守等待16:15后入库。

### PE 主源与对照

主源为雪球基金（原蛋卷）公开指数估值 API。官方说明为 PE TTM，主要取自 Choice；其页面描述用成分股市值总和除以 TTM 净利润总和。完整的加权、亏损处理和历史修订策略仍未独立核实。[雪球基金方法说明](https://danjuanfunds.com/article/337.html)

`pe_history/{SP500|NDX}?day=all&type=1` 返回约十年的周采样，`detail/{symbol}` 提供带日期的当前快照。时间戳编码的是上海零点的日期标签，不能转换到纽约后减一天。程序保存原始日期，按美国交易日历隔离非交易日（本轮标普50046条、纳指10045条 PE），不向前填充或移动到相邻交易日。

新增系列使用 `danjuan_index_implied_weekly_v1`：每个已结束自然周取最后一个有效正 PE，排除当前周和未来数据；均值及分位至少需要52个周观测。即使以后积累了每日快照，也不会把近期一周的五条数据与早期一周一条等权混算。所有均值和分位仅使用该主源，保留8倍 band。

2026-10-12对照结果：

| 指数 | 雪球基金主源 PE | 对照 PE | 相对主源差异 | 处理 |
| --- | --- | --- | --- | --- |
| 标普500 | 25.3298 | Multpl 26.64 | 5.1726% | 保留原值，停用当日 EPS/band 和该 PE 的统计贡献 |
| 纳指100 | 30.7803 | GuruFocus 28.93 | 6.0113% | 同上 |

[Multpl](https://www.multpl.com/s-p-500-pe-ratio)采用过去12个月 as-reported 盈利，当前值为估算；[GuruFocus 纳指100](https://www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio)公布日度 PE，但该页面未充分披露指数盈利与加权口径。另存 GuruFocus 标普500参考值26.46（与雪球相差4.4619%），不因第三个来源更接近而忽略 Multpl 的重大差异。差异原因尚未证实，不能断言其中一家错误。

GuruFocus 直接 HTTP 当前返回403；本次通过可读取的公开网页核实并保存10月12日标题中的数值，未使用页面中仍停留在10月9日的 Stats 数值。适配器只读取公开标题；失败会记录并保留已有历史，不绕过验证、不访问付费下载。网页参考快照不等于已建立稳定的自动核验源。

Multpl 自动采集仅导入当前带日期的估值，未将月度历史当成日度收盘 PE。乐咕公开图表亦以月度 PE 为主，日期标注与 Multpl 的月份起止不同，未导入该系列计算同日 EPS。Vanguard VTI 页面提供基金持仓 PE，不能直接视为 VTI 基准的指数 PE；VTI 基准暂保留价格展示。

未找到同日独立 PE 对照的历史日期可展示主源口径的参考 band，并明确标为口径未完全核实；已知重大 PE 差异的日期暂停派生。此限制依据 [financial-data 技能](/Users/zhanglei/.codex/skills/financial-data/SKILL.md) 的“误差 > 5%……不得直接使用”规则实现，不涉及额外审批。

## TradingView 官方 MCP 复核

2026-10-13已用授权账号实测 SPY 对应的 `SP:SPX` 和 QQQ 对应的 `NASDAQ:NDX`。两个指数最近5个已收盘交易日的价格与 Yahoo 差异均小于0.001%；当前 PE/PB/PS 为空，季度和年度财务历史均返回无数据。同一账号的苹果基本面可正常读取。现阶段可将其列为价格核验候选，不能作为指数估值主源。原始响应、雪球历史覆盖复核和同日 PE 差异见[完整验证记录](tradingview-spy-qqq-2026-10-13.md)。

## 运行与复用

三者纳入 `update-index --all`。配置分别声明价格源、附加来源、PE 主源、各供应商代码、时区、日期范围与 band 倍数；新增同源指数不复制算法。原富途六指数仍使用 `futu_index_implied_v1`；价格专用系列为 `index_price_only_v1`，防止方法混用。无需新增数据库迁移。

本机调度已安装：北京时间周一至周五21/22点、周二至周六7/8点。可执行：

```bash
uv run index-valuation backfill-index --index sp500
uv run index-valuation backfill-index --index nasdaq100
uv run index-valuation backfill-index --index ustotal
uv run index-valuation update-index --all
uv run index-valuation serve
```

网页按指数展示 ETF 映射、PE 主源、统计频率、采样日期和重大差异。均值/分位与历史回看共用同一后端计算；CSV 导出包含 `pe_source`、`pe_basis`、`pe_usable`、`history_frequency` 和核验状态。价格、原始 PE、派生 EPS 分别入库，原始文件按 SHA-256 保存于 `data/raw/`；详细任务报告、CSV及浏览器截图位于 `data/exports/`。连续价格保留在逐日表；稀疏 PE 图按实际采样日期展示，连线不代表补齐中间日度估值。

## 验证结果

117项 pytest 测试通过，包含15项 PostgreSQL 集成测试；Ruff、JavaScript 语法及 diff 空白检查通过。网页通过 Playwright WebKit 检查九个指数的切换、历史日期、固定/分位 band、均值、重大差异提示、跳转最近参考 band、CSV 和390px手机布局。未使用 Google Chrome。美股增量试跑中，标普/纳指行数保持不变；VTI 基准新增上游补齐的10月12日有效价格，未重复入库。
