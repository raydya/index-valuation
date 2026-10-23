# 网页性能优化：2026-10-23

本次优化总览的最新分位计算，以及网页图表响应的数据量。每次请求仍读取当前数据库数据；部署通过重建本项目的 Docker `web` 服务完成。

## 共用统计逻辑，只计算所需结果

`pe_statistics.PEHistory` 负责日频和周频的历史样本窗口。详情页逐日读取统计，再加入当天观测；总览使用 `latest_pe_statistics` 推进相同的窗口，只读取末日分位和样本数，不再构造历史曲线、估值带或收益摘要。

样本有序保存，分位插值使用 `quantile_sorted`，避免对已排序的数据重复排序。通用的 `quantile` 仍接受无序输入。日期边界、闰日、周频只使用已结束周、最少样本数、Decimal 累加与插值次序均保持原规则。

总览只查询当前方法近五年的日期、PE 和可用性字段；当前记录与历史查询的日期、PE、可用性不一致时，分位留空，防止并发修订时出现错配。短历史分位和完整五年恐慌／贪婪线仍有各自的资格要求。

## 网页专用响应

网页使用 `GET /api/indices/{key}/dashboard?view=chart`，可同时携带原有的 `as_of`、`chart_years`、`history_years`。逐日 `series` 仅返回三种图表模式、tooltip 和历史表实际使用的17个字段，由 `web_payload.py` 统一定义。

所有交易日、空值、数值精度、固定倍数（含8倍）、均值和分位曲线均保留。`selected` 及顶层的来源说明、核验、全收益摘要、恐慌／贪婪线保留完整内容，选择历史日期时仍可查看该日详细数据。响应采用紧凑 JSON，Decimal 继续序列化为原精度字符串。

默认 `view=full` 保留原接口，分析脚本和原始核验流程可继续使用。CSV 使用完整派生结果，保留逐日来源、口径、全收益身份和双线证据，不使用网页投影。

## 本机实测

Mac 上相同 Docker 网页和共享 PostgreSQL，接口各串行请求3次取中位数；页面使用3个独立 WebKit 会话。前后取同一份截至2026-10-22的估值数据。图表对照的是网页实际请求：优化前完整响应，优化后 `view=chart`。这是本机小样本对照，不代表其他机器或负载下的延迟。

| 请求或操作 | 优化前 | 优化后 |
| --- | ---: | ---: |
| 全指数现状总览 | 1,541 ms | 103 ms |
| 沪深300近5年图表 | 1,110 ms | 611 ms |
| 标普500近5年图表 | 1,060 ms | 666 ms |
| 沪深300全部历史图表 | 3,025 ms | 1,241 ms |
| 首页主要数据就绪（图表、总览、任务） | 2,293 ms | 710 ms |

沪深300五年响应解压后从5,287,636字节降到1,272,391字节；gzip传输从515,694字节降到280,416字节。全部历史解压后从24,573,722字节降到5,577,641字节，6,014个交易日全部保留。

本机结果保存在 `data/exports/performance-optimization/performance-comparison.json`，原始采样为 `data/exports/web-performance-{http,browser}-{before,after}.json`。性能数据记录于本报告，运行时不依赖这些文件。

## 核验方式

优化前保存所有指数的完整 dashboard、CSV 和代表日期回看；优化后逐字段对照，另将网页投影中的每个保留字段与优化前结果逐日比较。单元回归包含预先冻结的完整 JSON/CSV、日周频、五年边界、缺失与不可用 PE；数据库回归在独立临时 PostgreSQL 中执行。

```bash
uv run --locked pytest -m "not integration"
uv run --locked pytest -m integration tests/test_pipeline.py
PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/playwright" uv run --locked python scripts/check_web.py
PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/playwright" uv run --locked python scripts/check_market_overview_web.py
```

数据库测试要求已配置独立 `TEST_DATABASE_URL`；不得指向业务库。WebKit 验证实际网页使用 `view=chart`，并覆盖指数切换、历史日期、三种图表、固定倍数/均值/双线、CSV 和手机布局。

本次31个完整接口/CSV对照全部一致；14个最新或历史图表视图中的14,219条记录逐字段一致。1,013项非数据库测试、33项独立 PostgreSQL 集成测试及两项 WebKit 验证通过。对照记录位于 `data/exports/performance-optimization/`，临时测试数据库已清理。
