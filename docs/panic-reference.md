# 网页 PE 恐慌低点参考

每个指数页面在估值图下方复用同一模块：显示研究时的5年低10% PE 观察上限、所选日期 PE 与该线的差距、成熟3年信号的 PE 范围、随后3年价格年化中位数和1年内最差收盘浮亏。展开可查看非重叠窗口的日期与 PE、全部指数观察线对照，以及下载原始研究记录。

估值图上用红色水平直线显示恐慌估值参考：在“PE 倍数”和“历史分位”点位图中，线的高度为**所选估值日隐含 EPS × 记录的恐慌 PE 观察上限**；在“PE 走势”中，线的高度直接使用 PE 观察上限。标签显示倍数和对应点位，图表区间和历史统计窗口不会改变此水平值。更换估值日期时按所选日盈利重新换算；缺少可用 EPS 时不画点位线，PE 不可比较或历史回看早于研究日期时不画恐慌线。这是所选日的水平参考，不是历史逐日触发记录。

这些是[2026-10-14 研究](panic-ranges-2026-10-14.md)的固定记录，不是每日滚动恐慌信号。研究联合条件为当时此前5年 PE 分位≤10%，同时较近252个价格交易日收盘高点下跌≥20%。每个历史月份使用当时自己的分布，因此历史触发 PE 范围可能高于或低于记录的观察上限，不能把两者当作同一个固定买入区间。回报不含分红，尤其不能把红利低波的价格回报当作含息回报。

## 存储与展示

- 数值、来源、日期、输入与研究摘要哈希、口径说明保存于随包发布的 [panic-reference.json](../src/index_valuation/data/panic-reference.json)，不依赖忽略目录中的原始研究文件。没有写入或修改行情数据库。
- `/api/indices/{key}/dashboard` 的 `panic_reference` 字段包含对应记录、所选日期比较、全部已配置指数的简表。`/api/panic-reference` 下载完整研究记录。
- 差距 =（所选日 PE / 记录的观察上限 − 1）×100%，使用 `Decimal`；它是 PE 数值差距，不是价格还需下跌的幅度或买入信号。仅比较同来源、同口径的有效正 PE；停用、缺失或口径变更时留空，不补前一日数据。
- 记录不受图表1/3/5/10年、历史统计窗口或逐日更新影响。明确历史回看早于研究日期时，保留有日期的研究记录供查阅，暂停差距比较。默认最新页可以比较最新可用 PE，并注明其实际日期。
- 中证A500历史不足完整5年，不展示短样本分位冒充5年观察线；美国全市场没有 PE。无成熟回报样本显示空缺，不以0%替代。新配置指数没有研究记录时使用通用空状态，不需增加网页分支。
- 红利低波此记录沿用研究时的 `csindex_index_implied_daily_v1` 规则；研究后官方主源已调整为 v2，差异日继续参与当前统计。页面明确提示规则变化，保留旧研究结果，不能把它描述为现行规则下的新检验。

## 更新研究记录

日常 `update-index` 无需更新此记录。需要重新研究时，按[研究方案](panic-study-protocol-2026-10-14.md)在**新目录**创建快照并运行研究，再导出到新的审核文件：

```bash
.venv/bin/python scripts/analyze_panic_ranges.py data/exports/research/panic-next --snapshot
.venv/bin/python scripts/export_panic_reference.py \
  data/exports/research/panic-next data/exports/research/panic-next-web.json
```

导出工具不采集数据、不重算研究，核对每个输入哈希，并拒绝覆盖已有输出。数值从摘要提取；`notes` 默认为空。检查日期、口径、成熟样本数和风险后补充各指数的 `notes`，保留之前的研究报告，再将审核后的新文件替换到包内 `panic-reference.json`。新增指数若使用已有研究流程和来源，无需复制计算或前端逻辑。

重启本项目网页服务后，检查 API、下载记录、指数切换、历史日期回看、统计窗口切换和手机布局。维护与新快照相符的数值回归测试，通用行为测试继续复用：

```bash
.venv/bin/python -m pytest tests/test_panic_reference.py tests/test_index_series.py -q
node --check src/index_valuation/web/app.js
PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/playwright" .venv/bin/python scripts/check_web.py
```
