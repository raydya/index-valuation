# 网页恐慌、贪婪估值线与历史研究

每个指数页面展示所选日的5年低10%恐慌 PE 阈值、当前 PE 与阈值的差距、对应指数点位及实际样本区间，并提供全部指数当前线对照。所有指数共用计算和展示，不需要逐个手填阈值。

同一滚动窗口另计算90%分位的贪婪线（`rolling_pe_p90_v1`），与恐慌线共享有效样本、完整性和数据质量规则。页面同时展示绿色恐慌线和红色贪婪线；顶部全部指数状态按两条线分类，详见[指数现状总结](market-overview.md)。原恐慌线的计算版本与含义不变。

## 每次估值更新自动重算

计算版本 `rolling_pe_p10_v1` 使用所选日 D 之前5年的有效正 PE，窗口为 `[D−5年, D)`；只纳入本方法、同来源和同口径的正式发布后数据，排除当天、未来和发布前回溯记录。最早有效正式 PE 必须覆盖5年，窗口内还须满足 `band_min_observations`。不足时不以短期分位冒充5年阈值。完整性标记表示跨度和样本数满足要求，不声称所有交易日无缺口。

日度方法按有效交易日等权；周度方法取已结束自然周内最后有效值，不把本周数据加入本周阈值。10%分位采用位置 `(n−1)×0.1` 线性插值，全部计算使用 `Decimal`。历史 PE 口径差异是否影响有效性，沿用各自方法的 `pe_usable` 政策，不混合其他来源补缺。

`IndexSeries.refresh` 在每次增量、回填及重建时，从所有已入库输入重新推导并保存逐日线；最近14天的采集窗口不会限制恐慌统计的5年窗口。历史修订会重算受影响日期，日期维度没有未来数据不等于具备未经修订的时点财务版本。仅需从现有数据库应用新逻辑时运行：

```bash
uv run --locked index-valuation build-index --index sp500
uv run --locked index-valuation build-index --index nasdaq100
```

日常使用已有 `update-index` 命令及定时任务，无需另设恐慌线任务。新增指数自动复用；暂停采集的美国全市场保留旧历史只读，不重建旧方法。

## 水平线、回看与缺口

在“PE 倍数”和“历史分位”点位图中，绿色恐慌水平线为**所选日可用隐含 EPS × 所选日恐慌 PE 阈值**，红色贪婪水平线为**同日隐含 EPS × 同日贪婪 PE 阈值**；在“PE 走势”中分别直接使用两个 PE 阈值。标签显示倍数和点位。图表区间及普通 band 统计窗口不会改变固定5年的计算规则。

历史回看读取历史日期自身的线，不使用今天的阈值。跨指数对照在回看时也限制到所选日及之前；最新视图按各指数实际数据日期展示。点位线是所选日盈利换算的水平参考，不是历史每一天都适用的固定价格。

- 差距 =（所选日 PE / 阈值 − 1）×100%，表示估值比值差距，不是买入信号或价格必须下跌的幅度。
- 当天 PE 缺失、不可用或口径不匹配时，不比较或画线；已有合格历史阈值仍可展示供查阅。
- 当天 EPS 缺失时，PE走势仍可绘线，点位线为空，不以其他日期盈利补值。
- 历史跨度不足5年或样本数不足时不设线。暂停指数不显示当前线，数据空缺不以0替代。

## 存储、API 与导出

- 每日线随当前方法存入 `index_series.details.panic_line`，保留计算版本、方法、来源口径、估值日、阈值、样本范围/数量、状态和对应点位；同主键更新，不增加重复行。
- `/api/indices/{key}/dashboard` 的 `panic_line` 是所选日线，`panic_overview` 是全部已配置指数的对照。网页直接读持久化结果，后续估值更新无需重启网页。
- CSV 追加 `panic_threshold_pe`、`panic_reference_points`、`panic_status`、`panic_as_of`、版本、来源与历史窗口字段，逐日保留原始精度；暂停指数输出暂停状态与空阈值。
- 初次上线计算或后端代码变更需重启本项目网页服务。实际首次重算结果见 [2026-10-16 记录](rolling-panic-lines-2026-10-16.md)。

贪婪线平行保存到 `index_series.details.greed_line`，并通过 dashboard 的 `greed_line`、`greed_overview` 和 CSV 的 `greed_` 字段提供。历史记录尚未重算时保持尚未计算，不借用其他统计窗口的 P90。顶部摘要由只读 `/api/market-overview` 提供，与历史图表的所选日期独立。

## 历史研究独立归档

页面另保留[2026-10-14 研究](panic-ranges-2026-10-14.md)的固定记录：当时观察上限、成熟信号 PE 范围、非重叠窗口、后续3年价格年化收益和1年内最差收盘浮亏。来源与日期保存在包内 [panic-reference.json](../src/index_valuation/data/panic-reference.json)；API 字段 `panic_reference` 与下载 `/api/panic-reference` 继续提供原记录。该归档不参与当前绘线，普通估值更新不重新运行收益研究。

原研究的联合条件是当时此前5年 PE 分位≤10%，同时较近252个交易日高点下跌≥20%。历史每个月使用当时自己的分布，成熟信号 PE 范围不等于某一日的固定观察线。旧雪球来源的美股研究不是新的富途 REST 数据验证；红利低波旧研究为 v1，当前官方主源为 v2，不能把旧样本收益称为当前方法的新检验。回报均不含分红。

需要重新开展完整收益研究时，按[研究方案](panic-study-protocol-2026-10-14.md)在**新目录**创建快照，再导出审核文件：

```bash
.venv/bin/python scripts/analyze_panic_ranges.py data/exports/research/panic-next --snapshot
.venv/bin/python scripts/export_panic_reference.py \
  data/exports/research/panic-next data/exports/research/panic-next-web.json
```

导出工具校验输入哈希并拒绝覆盖已有输出。检查日期、口径、成熟样本和风险，补充 `notes`，保留之前的研究报告，再更新包内归档。不能为了展示当前线而把旧研究的阈值换成今天的值。

## 验证入口

```bash
.venv/bin/python -m pytest tests/test_panic_lines.py tests/test_panic_web.py tests/test_panic_reference.py tests/test_index_series.py -q
.venv/bin/python -m pytest tests/test_pipeline.py -k panic -q
node --check src/index_valuation/web/app.js
PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/playwright" .venv/bin/python scripts/check_web.py
```

覆盖日期排除、来源和方法隔离、历史修订自动重算、周频、暂停/缺失状态、精确持久化、跨指数对照、窗口独立性、CSV及手机布局。
