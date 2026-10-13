# 数据与估值口径

本文主要说明成分重算方法 `published_weight_ttm_v1`。主网页现在使用独立的直接指数方法 `futu_index_implied_v1`，见 [直接指数估值与网页](direct-index-valuation.md)；本文的成分缺口阻断规则不作为直接指数流程的前置要求。当前实现面向人民币计价的沪深市场指数；沪深300是配置中的第一个实例。

## 数据来源与时间

| 数据 | 主来源 | 核验方式 |
| --- | --- | --- |
| 成分股及公布权重 | 中证指数官方 `closeweight.xls` | 成分集合与 Futu OpenD `get_plate_stock` 比对 |
| 个股收盘价格 | Futu OpenD `get_market_snapshot` | 同交易日 BaoStock 不复权日线 |
| 个股 TTM EPS | 富途收盘价 / `pe_ttm_ratio` | BaoStock 同日收盘价 / `peTTM` |
| 指数收盘点位 | Futu 指数快照 | BaoStock 同日指数日线 |

**EPS_TTM 是供应商 TTM PE 隐含的每股盈利，不是从原始财报重新计算的 EPS。** 数据中明确保存 `eps_method=price_div_pe_ttm`。这允许聚合成分公司的盈利收益率，但不能作为对供应商财报计算过程的独立审计。

富途 `earning_per_share` 的文档没有承诺它等于 TTM EPS，因此原值单独保存为 `reported_eps`，该列表示供应商原始每股盈利字段，未确认财报期，绝不直接代替 TTM EPS。后续接入带公告日期和股本口径的财报数据，可增加 EPS 方法版本，不必重写存储和聚合流程。

权重比例只有中证这一独立权威来源，状态为 `official_single_source`；成分名单的跨源一致，不代表权重已获第二个独立来源确认。两个行情供应商一致也不能保证上游财报完全独立或无错误。

每条记录保存交易日、源端行情时间、采集时间、来源、币种、复权口径和原始响应路径。原始响应以 SHA-256 文件名保存在 `data/raw/`。重跑产生新的任务审计记录；相同“来源、证券、交易日”的当前数据做 upsert，旧原始响应保留。

## 收盘与历史数据

- `run-daily` 只采集当天，按指数时区判断。交易日历取自 Futu；节假日跳过。
- 收盘前返回 `waiting_for_close`。停牌、旧日期、盘中报价或缺少有效正股字段均不会被当作当天完整收盘数据。
- 不把今天的财务快照回填到昨天，更不把当前成分倒推到历史日期。历史重算只使用已经存储的同日观测和不晚于该日的权重。
- EPS、PE 都属于供应商在采集时提供的数值；保存源端日期不代表具备未经事后修订的历史财报数据库。个股历史 EPS 使用下述公司估值序列推导，不能声称是当时已经公开、未经修订的财报数据。
- 每日 PE 使用同日未复权价格和对应每股盈利；不得把前复权价格除以未经同步调整的 EPS。历史 band 基于每日计算并保存的 PE 序列，不拼接不同复权口径的股价。

### 历史试跑与参考序列

`backfill-history` 从配置中的 `inception_date` 或指定 `--start` 抓取指数历史，终点默认昨天。它使用真实历史接口，逐页 / 逐年采集；不调用当前成分或当前 EPS 接口补写过去。

| 序列 | 接口 / 字段 | 入库口径 |
| --- | --- | --- |
| 富途指数日线 | `request_history_kline`，显式 `AuType.NONE` | 未复权指数收盘点位，EPS 空值 |
| 富途历史估值 | `get_valuation_detail`，`PE` / `YEAR_30` | `pe` / `futu_valuation_pe` |
| 中证历史行情 | `csindex-home/perf/index-perf` 的 `close` | 指数收盘点位 |
| 中证公布估值 | 同接口 `peg`，网站历史行情的滚动市盈率字段 | `pe_ttm` / `csindex_published_rolling_pe` |
| BaoStock 日线 / 日历 | `query_history_k_data_plus` / `query_trade_dates` | 独立点位核验、完整交易日序列 |

供应商估值进入单独的 `source_valuations`，包含来源、日期、指标、口径、采集时间、原始文件与任务 ID；不会被写入成分重算结果。中证 `indicator.xls` 中的总股本 P/E1、计算用股本 P/E2 也不能直接替换上述滚动指标。

价格在对齐交易日后按原有阈值检查。指数 PE 比较仅报告数值差异；即使差异 ≤1%，也不代表两家供应商的盈利期、加权方法和亏损处理完全一致。富途 K 线的 `pe_ratio` 和指数估值接口的 `PE` 均不直接冒充个股 `pe_ttm_ratio`。

报告保留每个来源的缺失日期区间、非交易日隔离记录和逐日校验。某个来源故障不导致其他来源改名替代它；已抓到的参考值可保留，任务仍明确标记 `partial`。没有当时成分权重及历史 EPS 的情况下，`component_recalculation.status=blocked`、`bands=null`，不会用今天的300只股票或最新权重倒推历史。

富途官方说明日 K 线仅提供最近20年，历史估值接口可选择30年区间；实际返回日期仍必须检查。本项目此次完整性验证以接口实测和数据库行数为准。[历史 K 线文档](https://openapi.futunn.com/futu-api-doc/quote/request-history-kline.html)、[估值详情文档](https://openapi.futunn.com/futu-api-doc/quote/get-valuation-detail.html)

### 个股历史 EPS / PE

富途[公司估值说明](https://support.futunn.com/topic497?from_platform=1&lang=zh-cn)明确，个股 PE 采用最近12个月归母净利润与总股数计算的每股收益。因此，公司 `get_valuation_detail(PE, YEAR_30)` 保存为 `pe_ttm / futu_company_pe_ttm_v1`；这项定义不扩展到指数加权规则。

`backfill-fundamentals` 逐股获取 PE、同日未复权价格和 BaoStock `peTTM` / 价格。沪深300使用获授权的东方财富 `klt=101,fqt=0` 价格；配置为 `futu` 时调用富途 `AuType.NONE` 日线。来源 Adapter 返回统一记录，后续核验、存储和估值不按指数代码分支。

```text
EPS_TTM[date] = unadjusted_close[date] / Futu_company_PE_TTM[date]
```

EPS 方法为 `price_div_pe_ttm`，属于 `vendor_implied_not_reported`。PE 的源端小数舍入会传递到 EPS，因此不能把数据库保留的小数位数当作财报精度。原始财报的基本 / 稀释 EPS 可能使用加权平均股数，口径与本方法不同。

1. 以完整交易日历及每只股票上市、退市日期确定应有日期；不以当前成分身份认定历史成员。
2. 校验证券、日期、币种与复权口径，再对价格、PE、EPS 分别独立核验。
3. 保留负 PE / EPS；零 PE、缺 PE 或缺同日价格不推算 EPS，不跨日填值。
4. 候选数值与 `verified` / `review` / `blocked` / `unverified` / `missing_price` / `missing_pe` 状态一同入库。超过5%的差异会阻止指数估值，但保留候选值便于审计。
5. 个股价格位于 `daily_observations`、富途 PE 位于 `source_valuations`、推导结果及三个来源文件路径位于 `daily_fundamentals`。`fundamental_batches` 按证券、区间、方法记录完成进度，事务回滚或中断后可重复执行。

配置使用富途历史价格时，价格观测的来源标识为 `futu_history`，日常快照仍为 `futu`，避免缺少 EPS 字段的历史 K 线覆盖已有财务快照。价格供应商仍是富途；该区别不影响同一套核验与计算流程。

`calculate --historical-fundamentals` 读取上述历史记录并重新核验。它仍按计算日选择不晚于当日且未过期的权重，缺少任意必要输入就停止出值；不会使用当前权重倒推。计算版本为 `published_weight_ttm_v1:futu_company_pe_ttm_<价格源>_v1`，与日常快照方法分别保存和累计 band 历史，报告提示供应商历史数据可能修订。

## 权重

官方文件的日期是权重日期，不能用下载日期替换。保留原始百分比 `weight_percent`，计算时使用：

```text
w_i = weight_percent_i / Σ weight_percent_i
```

全部权重必须为正，成分不可重复，数量须符合配置。权重合计只容许 0.2 个百分点以内的公布舍入差异；更大的偏差会拒绝入库。

若当日只有更早的官方权重，使用这些已公布比例计算**参考组合估值**，状态为 `estimated`，并输出权重日期与年龄。没有假装掌握每日自由流通股本、靠档因子和公司行动后的精确权重，也不以价格漂移算法冒充官方日权重。

默认权重超过 45 个自然日停止计算。成分调样后，若最新富途成分集合与权重表不同，也停止正式估值，等待完整的新权重快照。中证公开文件通常不足以重建完整历史成分和每日权重。

因此，本系统计算值是自定义的成分权重 TTM PE，不宣称复刻中证官方公布的静态 PE 或剔除亏损口径。后续取得每日计算用权重后，应增加可区分的方法版本。

## 多源核验

先检查日期、币种和复权口径，再按证券分别核对价格、TTM EPS；指数收盘点位也核验。误差使用 Decimal：

```text
relative_error = abs(primary - secondary) / abs(primary)
```

| 差异 | 处理 |
| --- | --- |
| ≤ 1% | `pass` |
| > 1% 且 ≤ 5% | `warning`，结果为 `review`，保存两个数值和误差 |
| > 5% | `error`，阻止生成 PE，需要进一步查原始财报或数据口径 |
| 任一来源缺失 / 日期不一致 | 阻止生成 PE，不用其他股票重新归一化来掩盖缺失 |

主源为零、另一来源非零时直接标为错误；两边都为零则一致。PE 为零视为供应商缺失或哨兵值，不能据此推断 EPS 为零。负 EPS 保留，亏损公司不会被静默剔除。

行情无权限、源端网络故障和数据缺失会出现在任务状态、日志与 JSON 报告中。BaoStock 只作核验，富途失败时不会自动升级为主源。

## 计算与 band

`w_i` 是归一化的市值比例，`P_i` 是价格，`E_i` 是 EPS_TTM：

```text
EY = Σ (w_i × E_i / P_i)
PE_TTM = 1 / EY
指数每份盈利（点数口径） = 指数收盘点位 × EY
某 PE 倍数对应点位 = 指数每份盈利 × PE 倍数
```

这等价于按持有股数聚合市值和盈利。直接做 `Σ(w_i × P_i) / Σ(w_i × E_i)` 会把市值比例误当作股数，结果不同。各股票 PE 的算术加权也不适用。

总盈利不为正时保留盈利收益率和盈利贡献，PE 与 band 返回空，状态为 `non_positive_earnings`。

系统输出两类 band：

1. **固定倍数参考点位**：倍数在 `indices.toml` 中设置，当前示例为 10、12、15、18、20、25 倍。这是情景刻度，不是历史分位或买卖信号。
2. **历史分位 band**：默认最近 1826 个自然日内、当日之前至少 252 条同方法的有效 PE，使用 10%、25%、50%、75%、90% 分位及线性插值。只纳入跨源检查通过的 `verified` / `estimated` 记录；`review`、缺失、失败和非正盈利结果不进入分布。

数据不足时返回 `insufficient_band_history`、已有数量和所需数量，历史分位为空。使用旧公布权重形成的历史序列仍是参考组合口径，不能称为官方沪深300历史 PE。

## 扩展方式

同类沪深中证指数：在 `indices.toml` 增加配置，`--all` 和每日任务自动纳入。证券日数据按证券和来源共享；成分历史和计算结果按指数分开。测试已用第二个指数配置验证同一条流程。

- `providers/` 模块负责外部字段、限流、时间与币种转换，返回统一 `Observation` / `WeightSnapshot`。
- `quality.py` 模块负责跨源检查。
- `valuation.py` 模块是纯计算 Interface，不读取数据库或网络，不按指数代码分支。
- `repository.py` 模块负责 PostgreSQL 事务、upsert、历史选择与任务锁。
- `pipeline.py` 模块编排上述 Interface；命令行和调度器都调用它。
- `history.py` 模块编排指数历史回填与缺口审计，共用来源 Adapter、存储、任务锁和误差比较；历史供应商 PE 与成分重算结果分表保存。
- `fundamentals.py` 模块编排个股历史价格、PE、隐含 EPS 与逐股进度，纯函数负责同日推导及来源核验；证券数据与进度可跨指数复用。

接入其他市场、不同权重来源或直接财报 EPS，需要新增相应 Adapter 和配置校验，通用存储、核验与计算保持复用。

参考文档：[富途快照字段](https://openapi.futunn.com/futu-api-doc/quote/get-market-snapshot.html)、[富途成分查询](https://openapi.futunn.com/futu-api-doc/quote/get-plate-stock.html)、[交易日历](https://openapi.futunn.com/futu-api-doc/quote/request-trading-days.html)、[BaoStock 官方项目文档](https://github.com/zxygithub/baostock/blob/master/docs/pythonAPI.md)、[中证沪深300官方权重](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/closeweight/000300closeweight.xls)。
