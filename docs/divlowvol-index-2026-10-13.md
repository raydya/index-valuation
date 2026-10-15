# 红利低波接入记录

核验日期：2026-10-13，北京时间收盘后。配置 key：`divlowvol`；[网页入口](http://127.0.0.1:8000/?index=divlowvol)。

本文原接入结果记录2026-10-13的 v1 行为；2026-10-14按用户要求改为官方数据优先，当前规则见文末修订，旧覆盖与阻断结论不代表 v2 结果。

## 指数身份与来源

接入中证红利低波动指数，简称红利低波，代码 **H30269**，人民币价格指数，50只成分、股息率加权。正式发布日2013-12-19，基日2005-12-30，基点1000。全收益版本代码为 **H20269**，本项目未混用该序列。依据：[中证官方单张](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/H30269factsheet.pdf)、[官方编制方案](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/H30269_Index_Methodology_cn.pdf)。本次下载单张的数据日期为2026-09-30，编制方案标注2020-12、V1.1。

| 来源 | 映射和作用 | 本次结果 |
| --- | --- | --- |
| 富途 OpenD | 检查沪深指数目录并试取 `SH.H30269` | 目录未收录；日线、PE 均返回 `Unknown stock. H30269`，不是行情权限错误 |
| 中证指数 | [历史接口](https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=H30269&startDate=20261009&endDate=20261014)，`indexCode=H30269` | `close` 为主点位，`peg` 为官方滚动 PE，按年采集 |
| 东方财富 | [红利低波行情](https://quote.eastmoney.com/q/2.h30269.html)，`secid=2.H30269` | 未复权价格指数日线，用于核验点位 |
| 雪球基金 | [估值详情 API](https://danjuanfunds.com/djapi/index_eva/detail/CSIH30269)，代码 `CSIH30269` | 周采样历史加每日快照，仅作为 PE 对照 |
| BaoStock | 内地交易日历 | 日历可用；未将内部 CSI 标识传给其 SH/SZ 日线接口 |

内部证券标识为 `CSI.H30269`，不伪装成富途支持的代码。雪球旧代码 `H30269` 的详情为空，历史只到2017年，已排除；通过公开指数目录核实后使用 `CSIH30269`。各供应商不等于各自独立重算指数，点位一致只验证传输值，PE 会计口径与上游独立性仍未完整确认。

## 实际覆盖

从基日发起完整回填，过滤市场日历后如下：

| 数据 | 行数 | 起止日期 |
| --- | ---: | --- |
| 中证官方点位 | 5,044 | 2006-01-04 — 2026-10-13 |
| 东方财富点位 | 3,113 | 2013-12-19 — 2026-10-13 |
| 中证官方滚动 PE | 3,123 | 2013-12-05 — 2026-10-13 |
| 雪球参考 PE | 513 | 2016-10-13 — 2026-10-13（稀疏采样） |
| 可用隐含 EPS | 3,008 | 2013-12-19 — 2026-10-12 |

基日2005-12-30没有实际返回点位，未人工补造1000点记录。正式发布前的价格和 PE 标为 `backcast`；2013-12-19前没有第二价格源，不能生成 EPS。已发布后的3,113个价格日期全部通过双源核验，最大相对差异约0.000160%。没有把 ETF 价格加入指数历史。

PE 的513个同日对照中，70个差异≤1%，338个差异在1%—5%，105个超过5%。另外2,610个官方 PE 日期没有有效同日 PE 对照，不等于已独立核实。主源日度记录不填充参考源采样空白。

隔离39条日历外记录：中证点位22条、中证 PE 14条、雪球 PE 3条。原始响应保留，日期没有搬移到相邻交易日。数据范围指本次接口实际可取得并入库的范围，不承诺供应商不存在其他授权历史。

## 计算与当前限制

复用通用 band 算法，增加独立方法 `csindex_index_implied_daily_v1`，保持来源和历史与富途方法隔离。固定倍数为6、8、10、12、15、18、20；均值、分位使用此前同口径日度正 PE，最低252个样本，默认5年窗口。

同日点位须经第二来源核验；已知 PE 差异超过5%时，保留官方 PE 与参考值，设置 `pe_usable=false`，暂停当天隐含 EPS、band 和百分位，且该 PE 不贡献后续统计。既有富途方法保持其原有政策。

2026-10-13官方收盘点位为11,217.58（东方财富11,217.59），官方 PE 为8.31，雪球参考 PE 为8.8323，相差 **6.2852%**。官方说明采用计算用股本，第三方盈利、权重与亏损处理差异尚未完全解释，因此当天 EPS/band 留空。网页仍显示官方 PE 和历史均值，并提供2026-10-12的参考 band 入口；该日无同日雪球对照，不能称为已独立验证的 PE。

## 运行与验证

```bash
uv run --locked index-valuation backfill-index --index divlowvol
uv run --locked index-valuation update-index --index divlowvol
uv run --locked index-valuation status --index divlowvol
```

先试跑2026-10-09至10-13，再从基日全量回填，随后实际运行增量。增量从2026-09-29开始；重跑后5,044条派生记录、各来源价格及 PE 行数均不变，没有重复入库。最新 PE 质量受限使状态为 `partial`、退出码2，来源采集本身成功；富途、BaoStock 报价明确标为 `unsupported`。

配置自动进入 `update-index --all` 和现有每日调度，无需新增定时任务。网页服务重启后加载该配置，平时增量无需重启。

本次记录与证据位于本地忽略目录：

- `logs/divlowvol-pilot.json`、`logs/divlowvol-backfill.json`：试跑、完整回填及各源覆盖；内含详细采集报告路径。
- `logs/divlowvol-incremental.json`、`logs/divlowvol-incremental-check.json`：增量范围和更新前后行数。
- `data/raw/csindex_identity/`、`data/raw/divlowvol_futu_probe/`、`data/raw/danjuan_index_directory/`：官方 PDF、OpenD 不支持响应和雪球目录；行情响应另按来源及哈希归档。
- `data/exports/divlowvol-index-bands-2026-10-13.csv`：完整派生序列，包含 PE 来源、核验、缺口和 band。

验证包括错误指数映射、价格版与全收益版隔离、官方 PE 不与自身对照、缺失值不替补、重大差异隔离、日度统计不受参考源周采样影响，以及不支持报价时交易日历仍可使用。单元测试116项和 PostgreSQL 集成测试15项通过，Ruff 静态与格式检查通过。

WebKit 对全部10个指数的切换、刷新、PE/分位/平均 band、日期回看、CSV 和手机布局检查通过；红利低波的重大差异提示及回看最近可用 band 的入口实际可用，CSV 与 API 数值一致，无 JavaScript 错误。报告见 `data/exports/web-validation.json`，截图为 `data/exports/divlowvol-web-desktop.png` 和 `data/exports/divlowvol-web-mobile.png`。

## 2026-10-14：官方数据优先

按用户要求，来源存在差异时以指数官方数据为准。新方法为 `csindex_index_implied_daily_v2`，v1 派生记录及原始来源记录保留。使用全部已入库数据执行 `build-index --index divlowvol` 重建，历史均值与分位也按新规则恢复官方正 PE 样本；其他主源方法保持独立。

- 官方滚动 PE 继续作为唯一主 PE，第三方差异超过5%仍保留对照值、差异比例和 `error` 标记，但 `pe_usable=true`，不再因此阻止 EPS、band 或统计。
- 有效官方点位优先；东方财富缺失或有差异时仍可用于计算，`price_usable=true`。`price_status` 保留未核验或差异状态，不宣称第三方校验已经通过。
- 官方 PE 缺失、为零或非有限值不替补；官方点位缺失后的第三方备用点位仍须通过独立核验。原有证券身份、交易日历、收盘时间和负 PE 规则继续生效。
- 网页、API、CSV 明确显示官方来源和对照差异，CSV 增加 `price_usable`。价格收益也遵守已选官方点位的计算资格，差异提示保留。

实际重建截至2026-10-14：v1、v2 各保留5,045条派生记录，原始点位、PE 及对照值逐日一致，无重复主键；可用隐含 EPS 从3,008条恢复为3,124条。106个重大 PE 差异仍保留 `error` 标记。当天官方点位11,295.34、PE 8.33，隐含 EPS 为1,355.9831932773，8倍点位10,847.8655462185；近5年统计使用此前1,211个有效日度 PE，均值约6.41857，当前分位约98.5549%。原始 PE 和点位没有被第三方值替换。

145项单元测试、16项 PostgreSQL 集成测试通过，Ruff 和 JavaScript 语法检查通过。红利低波的 WebKit 检查覆盖官方优先提示、保留差异、当日及10月13日回看、8倍/均值/分位 band、PE 图、CSV、刷新和手机布局，未出现 JavaScript 错误。全站脚本另在沪深300缺少当日 EPS 时触发了其“最新日必有 band”的既有断言，本次红利低波检查独立运行通过。

本次证据：`logs/divlowvol-official-priority-2026-10-14.json`、`logs/divlowvol-official-priority-verified-2026-10-14.json`、`data/exports/divlowvol-official-priority-web-2026-10-14.json`。网页接口与磁盘 CSV 的点位、PE 及质量字段一致；历史隐含 EPS 和 band 仅有 PostgreSQL 16位小数存储带来的末位舍入差异（小于 `1e-14` 点）。
