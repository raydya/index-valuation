# 指数直接估值与网页

方法 `futu_index_implied_v1` 使用供应商发布的指数整体估值，不依赖成分、权重或公司 EPS。指数配置驱动采集、存储、计算和网页，算法不按沪深300代码分支。

## 数据与计算

- 富途 `request_history_kline(AuType.NONE)` 提供日收盘点位；`get_valuation_detail` 分别请求 PE、PB、PS 和 YEAR_30，保留全部原始响应及元数据。
- 中证指数、国证指数历史 `close`、BaoStock 日线、东方财富未复权日线核验点位。按富途、中证、国证、BaoStock、东方财富的顺序选择可用点位；只请求配置适用的独立来源，需要另一来源同日核验通过才推导 EPS。相差1%—5%保留 warning，超过5%或没有参照价格则不生成 EPS。
- 富途 PE 为唯一主 PE 序列，缺失和零值不从中证替换。中证滚动 PE 单独保存和比较。PB、PS 保存为富途原始估值，目前未独立核实其财务口径。
- 所有来源按交易日历过滤；周末、节假日等异常源记录保留在原始文件，并写入隔离报告，不移动日期。日线日期不得在尚未收盘的未来。

```text
隐含 EPS[t] = 同日指数收盘点位[t] / 富途指数 PE[t]
固定倍数点位[t, m] = 隐含 EPS[t] × m
平均 PE[t] = 此前窗口内同源每日正 PE 的算术平均
平均 PE 对应点位[t] = 隐含 EPS[t] × 平均 PE[t]
历史分位点位[t, q] = 隐含 EPS[t] × quantile(此前同源正 PE, q)
```

隐含 EPS 的单位是指数点。指数 PE 的盈利期、加权及亏损处理仍未完整确认，因此不写成 EPS TTM。反算 EPS 不构成对 PE 的独立财报审计，band 表示指定倍数下的参考点位。

## 历史平均 PE 与分位

统计窗口默认5年，可选3年、5年、10年及全部历史，同时控制平均 PE 和分位，与图表展示区间独立。日历年窗口左端包含、右端严格小于当天。2月29日回退到对应年的2月28日。每个历史日期均重新使用自己的当时窗口，不能用今天的平均 PE 或分位阈值画整段历史。

平均 PE 对每个有效交易日等权，保留极端正值，不截尾。至少252个历史样本才展示均值。图中虚线在点位视图表示当日隐含 EPS × 当时的历史平均 PE，在 PE 走势视图直接表示历史平均 PE。均值可在当日缺少价格或 EPS 时显示，此时对应点位留空。它衡量指数自身历史估值，不计算成分股 PE 的横截面平均。

当最早可用正 PE 晚于统计窗口左端时，`history_span_short` 标为 true，网页提示“历史不足N年”并展示实际样本范围。统计仍按已有有效样本计算，不补齐缺失年份。例如新发布指数虽有多年回溯点位，未必有同样长的 PE 历史。该标记只描述可用历史跨度，不代表已经检查每个交易日的完整性。

有效样本为同一富途口径的正 PE，即使那天缺价格，也可贡献 PE 分布。至少252个历史样本才展示分位。分位阈值使用线性插值（位置 `(n-1)q`）；当前分位使用 `(小于当前值的数量 + 等于当前值的数量/2) / n × 100%`。负 PE / EPS 保留，但不生成正倍数 band 或当前百分位。历史窗口包括接口提供的发布前回溯 PE，网页逐日标记其性质。

历史 PE 与中证差异超过5%的记录仍显示为富途自身口径的原始估值，并显式标记来源差异和口径未核实；不称为已验证的官方 PE。两家口径不同的序列不混合计算分位。供应商可能修订历史；排除未来日期不意味着具备当年已公开、未经事后修订的 point-in-time 财务数据。

## 存储和更新

- `daily_observations`、`source_valuations`：原始点位和估值，保留来源、日期、文件路径。
- `index_series`：主键为指数、交易日、方法；保存选定点位、PE、隐含 EPS，以及 JSON 来源、PB/PS、核验状态、回溯标记。重复运行更新相同主键，保留原始响应和运行记录。
- `daily_valuations`：原有成分重算结果，完全独立，不写入直接指数结果。

`backfill-index` 默认从配置 `history_start_date`（缺省用 `inception_date`）到最近已收盘日期；`update-index` 从最后已入库日回退14天更新，长时间停机也会覆盖后续完整缺口。若已有价格但还没有任何 PE（例如首次缺少权限），增量命令仍保留完整回填起点，以便权限恢复后补齐历史。两个命令随后重新派生全部指数序列。更早修订通过全量刷新获取。`build-index` 仅从现有数据库重建。

采集与派生各有任务锁和运行记录。一个数据源失败时保留其他来源及已有数据，最终报告 `partial` 并附采集报告；不会把旧数据重命名成最新日期。`collected` 表示技术流程完成，不是财务口径验证结论。

内地交易日历优先从 BaoStock 获取；其不可用时，先复用完整覆盖请求区间的历史日历归档，校验 SHA-256、完整自然日标记、重复日期及观测时间。缓存不向未覆盖日期外推。近期增量超出缓存范围时可使用富途 A 股交易日历，保留独立来源与原始响应；富途只允许过去10年范围，临时休市不在其日历保证范围内。所有备用路径均记录原因，不隐瞒 BaoStock 故障。没有可用日历时终止采集，不按工作日猜测交易日。

香港使用富途 HK 市场日历，不使用 BaoStock 或内地假期。更早的香港日期使用富途恒生指数 `HK.800000` 实际日线的日期，保留基准代码和日线原始文件；这属于实际历史交易日期参照，不宣称是交易所完整日历。富途日历中误列为开市的2016-10-21、2017-08-23，依据港交所全日停市公告排除，修正日期与公告链接记入日历归档。香港按16:10后采集当日数据；半日市同样保守等待至此时间。HKD、CNY 分别保存，隐含 EPS 和 band 始终以各自指数点位计算，不需要汇率换算。

中证参考历史分年采集；个别年份 HTTP 请求失败时保留其他年份并记录 `failed_ranges`，来源标为 `partial`。连续3个请求失败则停止后续年份并记录尚未尝试区间。它不会使其他价格来源或富途估值丢失。

网页是只读 FastAPI 应用，使用数据库中的派生序列。HTML/CSS/JavaScript/SVG 全部随应用提供，不需要外部图表或字体 CDN，也不把数据库密码发送到前端。默认仅在 `127.0.0.1:8000` 监听；采集由命令行或已有定时任务运行。

接口：`GET /api/indices`、`GET /api/indices/{key}/dashboard`、`GET /api/indices/{key}/export.csv`。后两者接受 `as_of`、`chart_years` 和 `history_years`。非交易日选择解析为不晚于请求日的最后一个记录日；早于数据起点返回明确错误。CSV 使用 UTF-8 BOM，保存原始数值精度、来源和质量状态。`mean_pe`、`mean_points` 分别为历史平均 PE 与对应点位；`history_years`（0为全部历史）、`history_count`、`history_start`、`history_end` 保留统计窗口及实际样本范围。

## 本地运行与验证

```bash
uv run index-valuation backfill-index --index csi300
uv run index-valuation serve
uv run pytest
uv run alembic check
```

网页交互验证使用 Playwright WebKit，不使用 Google Chrome：

```bash
PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/playwright" uv run playwright install webkit
PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/playwright" uv run python scripts/check_web.py
```

数据接口参考：[富途估值详情](https://openapi.futunn.com/futu-api-doc/quote/get-valuation-detail.html)、[富途历史 K 线](https://openapi.futunn.com/futu-api-doc/quote/request-history-kline.html)。
