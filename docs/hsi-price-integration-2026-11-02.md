# 恒生科技官方价格核验接入

2026-11-02，恒生科技的富途 OpenD 价格和 PE 已更新至10月30日，但东方财富价格参考持续断连，
导致10月23日起6个交易日无法通过同日价格核验，隐含 EPS 和 band 留空。
小窗口试取曾成功，实际增量再次断连，因此改用已核实的恒生官方价格历史作为长期核验来源。

## 指数身份与接口

官方 [Hang Seng TECH Index 页面](https://www.hsi.com.hk/en-hk/indexes/all-indexes/index-details/?seriesCode=hstech)
和[指数单张](https://www.hsi.com.hk/static/uploads/contents/en/dl_centre/factsheets/hsteche.pdf)
确认指数为 HSTECH、原生币种 HKD、发布日期2020-07-27、基日2014-12-31。
公开历史接口返回的代码与名称分别为：

| 官方 API 代码 | 官方名称 | 用途 |
| --- | --- | --- |
| `02083.00` | Hang Seng TECH Index | 价格指数，核验 `HK.800700` 的收盘点位 |
| `12083.00` | Hang Seng TECH Index (Gross Total Return Index) | 原有税前全收益映射，仅用于分红再投资收益 |
| `22083.00` | Hang Seng TECH Index (Net Total Return Index) | 净收益版本，不用于本项目上述两项计算 |

价格请求使用[恒生官方历史接口](https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy/v1/product-data/pub/indexes/metadata/v2?data=valueHistoryPub&indexCodes=02083.00&language=en-hk)，
参数为 `data=valueHistoryPub`、`indexCodes=02083.00`、`language=en-hk`。
读取 `valueHistory` 的真实 `date` 和 `close`，不用实时指数值、ETF、全收益或净收益点位替代。
接口自身没有币种字段，币种由已核实的指数身份和 HKD 配置确定，并非由响应直接提供。

本次接口提供1230条日收盘，实际范围为2021-10-29至2026-10-30。
这是约5年的滚动窗口，没有日期分页参数；不能宣称覆盖自基日起的完整历史。
请求窗口早于可用起点时保留可用行并报告 `unsupported_historical_coverage`。

## 配置与数据边界

恒科配置为 `reference_history_source = "hsi"`、`hsi_symbol = "02083.00"`，
`historical_price_source = "futu"`。移除恒科 `eastmoney_secid` 以停止冗余且持续失败的主动请求；
不删除或改写已有东方财富原始输入，旧日期仍能使用已入库的同日核验数据，其他指数配置不变。

新增 `HSIPriceHistory` 与原 `HSITotalReturnHistory` 共用 `hsi_history_base.py` 的请求、归档、
身份、日期及数值校验。两个配置分别限定价格版与税前全收益版，检查官方代码和完整名称，
并验证市场、币种、时区和内部证券身份。价格记录采用 `source=hsi`、
`price_basis=unadjusted_close`，保存在 `daily_observations`；全收益仍保存到
`total_return_observations`，不会进入估值输入。原始响应按内容哈希分别归档至
`data/raw/hsi_price_history/` 和 `data/raw/hsi_total_returns/`。

富途仍是采用的价格及 PE 来源，方法保持 `futu_index_implied_v1`，未改变其盈利期间、加权或亏损口径。
HSI只提供价格核验，不提供或代填 PE。与富途同日价格差异≤1%通过、1%—5%警告、>5%或缺少对照时
EPS/band仍留空；没有放宽质量门槛。未收盘数据不入库，市场日历继续来自富途 HK 日历。

## 恢复结果与验证

通过既有 `update-index --index hstech` 重叠采集、upsert和整条派生重建恢复了
2026-10-23、26、27、28、29、30六个交易日的 EPS/band。
六天官方与富途点位逐日完全相同，最新完整估值日期由10月22日推进至10月30日。
10月30日富途点位3754.90、PE19.995，隐含 EPS 为187.7919479869967492指数点。

验证覆盖价格/全收益/净收益身份隔离、无效或重复日期、未收盘行、非正/非有限价格、
HTTP/网络失败、滚动覆盖不足，以及同日核验通过/警告/重大差异对派生结果的影响。
PostgreSQL集成测试使用独立临时 `_test` 数据库，检查重复upsert不新增主键、
官方参考价入库后恢复富途EPS、保持原PE方法与来源。配置与代码需重建网页镜像后生效。
