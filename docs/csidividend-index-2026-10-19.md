# 中证红利接入记录

核验日期：2026-10-19。新增配置 key 为 `csidividend`，页面为 [中证红利](http://127.0.0.1:8000/?index=csidividend)。复用现有富途适配器、采集编排、数据库、估值带、滚动双线和网页，没有新增按指数名称分支的算法。

## 指数身份与配置

- 正式名称：中证红利指数 / CSI Dividend Index，官方代码 `000922`，100只样本，股息率加权。
- 正式发布日：2008-05-26；基日及本次历史请求起点：2004-12-31。发布前记录保留 `backcast` 标记。
- 展示人民币价格指数，市场 `CN`，时区 `Asia/Shanghai`，收盘时间15:00。全收益指数为 `H00922`，不混入当前价格序列，页面年化收益不含分红。
- 富途 `SH.000922` 实际返回名称 `CSI Dividend Index`、类型 `IDX`、市场 `CN_SH`。其基本资料中 `listing_date=1970-01-01` 不用作指数发布日期。
- 中证对照代码 `000922`、BaoStock `sh.000922`、东方财富 `1.000922`；均验证同日价格对应同一价格指数。
- 固定倍数为6、8、10、12、15、18、20倍；历史均值/分位沿用至少252个先前观测。恐慌与贪婪线使用此前5年同口径有效正PE的P10/P90，排除当天、未来和发布前回溯记录。

身份依据：[中证官方单张](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000922factsheet.pdf)、[官方编制方案（本次取得2023-12 V1.3）](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/20231208175433-000922_Index_Methodology_cn.pdf)。与现有红利低波的区别见[对比记录](csi-dividend-vs-lowvol-2026-10-19.md)。

## 来源与口径

继续使用 A 股已有的富途 OpenD 主源设置：`valuation_source = "futu"`，派生方法 `futu_index_implied_v1`。日线提供价格，估值详情分别提供PE/PB/PS；隐含EPS等于同日选定点位除以富途PE。中证官方点位及滚动PE单独对照，BaoStock和东方财富用于价格核验及已授权的历史补源。

富途价格缺失时沿用通用价格回退规则，参考点位必须取得第二来源核验才能推导EPS/band。富途与中证PE的盈利期间、加权及亏损处理尚未完整确认，不混合两套PE历史，不将隐含EPS称为已核实的公司TTM盈利。历史PE差异超过5%保留差异标记，富途方法仍使用自身口径。

2026-10-12至10-16短区间试跑完成，五个交易日均入库。价格经富途、中证、BaoStock、东方财富对照通过；PE数值对照3日通过、2日警告，仍不代表财务口径相同。试取证据：`data/source-research/csidividend-probe-2026-10-19.json`。

## 本次实际覆盖

2026-10-19上午执行，最近已收盘的A股交易日为2026-10-16。以下为数据库保存结果，不表示各字段没有历史缺口：

| 数据 | 最早日期 | 最新日期 | 有效记录数 |
| --- | --- | --- | ---: |
| 选定指数点位 | 2004-12-31 | 2026-10-16 | 5,290 |
| 富途PE / PB / PS（各） | 2005-01-04 | 2026-10-16 | 5,284 |
| 隐含EPS及正倍数估值带 | 2006-10-09 | 2026-10-16 | 4,861 |
| 富途日线 | 2006-10-09 | 2026-10-16 | 4,861 |
| 中证官方点位 | 2004-12-31 | 2026-10-16 | 5,290 |
| 中证官方参考PE | 2011-06-28 | 2026-10-16 | 3,650 |
| BaoStock点位 | 2008-05-26 | 2026-10-16 | 4,469 |

共有821条发布日期前回溯记录和4,469条正式发布后记录。富途PE缺少2004-12-31，以及2019-01-31、02-01、02-11、02-12、02-13，未以中证PE填补。424个早期日期只有单一价格来源，相关EPS/band为空；其余有价格但没有PE的日期同样留空。

截至2026-10-16，收盘点位5580.7083，富途PE为9.017，隐含EPS约618.90965。对应滚动恐慌PE为5.075、贪婪PE为8.529，均使用2021-10-18至2026-10-15的1,211个先前有效观测。阈值是本次数据快照，日后每次估值更新自动重算，不写死在配置里。

## 请求失败、隔离及保留边界

- 首轮全历史回填中证成功，东方财富请求中断；重试时中证返回HTTP403，东方财富仍断开连接。未绕过访问限制，已存数据保留。最近短区间的东方财富价格成功记录仍保留，但没有声称其全历史采集成功。
- 随后实际执行增量，富途价格、PE/PB/PS及BaoStock成功；中证对照返回HTTP403、东方财富断开连接。命令返回2、批次标记 `partial`，而最新主源价格、PE、EPS齐备。第三方失败未删除前次成功的官方记录。
- 首轮采集有39条日历外记录被隔离，包括富途2014-12-20日线，以及中证早期非交易日记录。保留原始响应和隔离报告，没有挪到相邻交易日。
- 全历史PE对照：84日差异≤1%，736日差异1%—5%，2,825日差异>5%，1,645日缺少可配对数据。各源原值分别保留；“技术采集完成”与“口径已核实”分开。

关键运行证据（本地数据目录不提交Git）：

- 首轮全历史报告：`data/exports/csidividend-history-2004-12-31-2026-10-18-45d89ddc-8314-4047-a9de-f4828f372912.json`
- 增量采集报告：`data/exports/csidividend-history-2026-10-02-2026-10-18-d0024f5f-a2d6-4c18-978e-5e8f0aa9a4b1.json`
- 最终派生报告：`data/exports/csidividend-index-series-e82cb0e6-45be-443c-a8dc-1d4172a23e1a.json`
- 逐日导出：`data/exports/csidividend-index-bands-2026-10-16.csv`

## 日常更新与验证

`csidividend` 自动进入 `update-index --all` 和 `--market CN HK`，沿用北京时间每日17:00的A股/港股更新任务，不需重装调度。新增配置后已重启本地网页服务。

```bash
uv run --locked index-valuation update-index --index csidividend
uv run --locked index-valuation status --index csidividend
```

执行过短区间回填、完整历史回填及实际增量；现有配置、市场日历、调度、估值序列、滚动双线、市场总览相关单元测试95项及PostgreSQL集成测试24项通过。

最终逐条核对5,290条数据库、API与全历史CSV的日期、点位、PE、EPS、PB、PS；8倍band、双线阈值及发布日期前标记一致，增量前后日期集合和记录数不变、无重复。验证产物为 `data/exports/csidividend-integration-verification-2026-10-19.json`。

现有 `scripts/check_web.py` 与 `scripts/check_market_overview_web.py` 的WebKit检查均通过，覆盖新增指数的切换/刷新、8倍/均值/分位band、PE走势、双线、CSV、手机布局和市场总览，没有JavaScript错误。本次网页选择的5年区间导出1,212行，与API一致；总览包含全部11个配置指数。产物为 `data/exports/csidividend-web-validation-2026-10-19.txt`、`data/exports/csidividend-market-overview-web-validation-2026-10-19.txt`，截图为 `data/exports/csidividend-web-desktop.png` 和 `data/exports/csidividend-web-mobile.png`。
