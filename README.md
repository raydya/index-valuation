# 指数估值系统

使用 Python、PostgreSQL 和本地网页构建指数估值应用：采集指数每日点位、PE / PB / PS，推导隐含 EPS，展示固定倍数和历史分位估值带。

主流程采用指数整体估值，不依赖成分股数据。内地、香港指数以 Futu OpenD 为主；美股指数使用明确配置的价格、PE 来源。支持三个市场各自的交易日历与时区。原有成分股采集与重算流程保留为独立分析能力。指数通过 `indices.toml` 配置，新增同类指数可复用计算与网页。

**指数隐含 EPS = 同日收盘点位 / 配置主源的指数 PE。** 单位为指数点；供应商盈利、加权和亏损处理尚未全部独立核实。不同主源使用独立方法版本，参考 PE 不混入主源历史，缺失值不填充。详见 [直接指数估值说明](docs/direct-index-valuation.md)。

[各指数恐慌区间历史检验](docs/panic-ranges-2026-10-14.md)比较低 PE 分位、大幅回撤及其联合条件之后的价格回报，包含来源口径、短历史、重叠样本和继续下跌风险检查。通用入口 `scripts/analyze_panic_ranges.py` 支持对全部配置指数只读导出快照并复跑；完整方法和命令见报告。

每个指数页面展示“滚动恐慌估值线”：使用所选日之前5年、同一来源口径的有效 PE 的10%分位，每次估值更新自动重算并保存。图中以水平线展示对应 PE 或当日隐含 EPS 换算的点位，支持各指数对照与历史回看；历史跨度不足5年时不设线。原有恐慌研究的成熟样本和后续回报独立保留为注明日期的历史归档。[计算规则与记录维护](docs/panic-reference.md)。

页面顶部的“指数现状总结”按各指数最新入库数据展示估值灯号：绿色“恐慌，持续买入”、黄色“普通，继续持有”、红色“贪婪，减仓离场”。绿色恐慌线采用此前5年 PE 的 P10，红色贪婪线采用同一样本的 P90；两条线随每日更新保存，图中支持点位和 PE 模式及历史回看。缺少有效 PE、历史不足或暂停采集时显示灰色待判定，不用旧日期补值。[灯号规则与页面说明](docs/market-overview.md)。

## 技术栈

| 用途 | 工具 |
| --- | --- |
| 运行环境 | Python 3.12、uv、项目内 `.venv` |
| 数据库 | PostgreSQL 17，由独立的 `../local-infra` Docker Compose 管理 |
| 数据访问 | SQLAlchemy 2.0、Psycopg 3 |
| 结构迁移 | Alembic |
| 配置 | pydantic-settings，读取 `.env` 与环境变量 |
| 网页 | FastAPI、Uvicorn、原生 JavaScript / SVG，无 CDN 依赖 |
| 开发检查 | Ruff、pytest |

依赖的确切版本保存在 `uv.lock`；日常安装使用 `uv sync --locked`。Python 小版本由 `.python-version` 固定，补丁版本可更新。正式 PostgreSQL 的镜像版本、升级和数据卷由 `../local-infra` 统一管理。

## 首次启动

前置条件：安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和可用的 Docker Engine / Docker Desktop（含 Compose v2）。Docker 服务需处于运行状态。生成配置的脚本兼容系统 Python 3.9+；应用使用 Python 3.12，缺失时 uv 会自动安装。

先按 `../local-infra` 的说明启动共享 PG，创建本应用专用的 `index_valuation` 数据库、运行账号 `index_valuation_runtime` 和迁移账号 `index_valuation_owner`。本项目不会自行启动或停止共享 PG。

在项目根目录执行：

```bash
make env
make install
# 在本机 .env 填入 infra 分配的 POSTGRES_PASSWORD 和 MIGRATION_DATABASE_URL。
make setup
```

`make env` 创建权限为 600 的配置模板，保留已有文件；不会为已存在的共享数据库生成新密码。`make setup` 安装依赖，等待共享数据库连接，使用独立迁移账号应用 Alembic 迁移，最后检查运行账号连接。连接输出不包含密码。

正式默认地址为 `127.0.0.1:15432`，数据库为 `index_valuation`，运行账号为 `index_valuation_runtime`。端口只绑定本机；正式数据卷、备份和恢复在 `../local-infra` 管理。独立库保留 UTF8 编码、`C.UTF-8` 排序规则和 UTC 时区。

也可以分步执行：

```bash
make db-up       # 只等待连接，不控制容器生命周期
make migrate     # 只使用 MIGRATION_DATABASE_URL
make db-check    # 使用 POSTGRES_* 运行账号
```

成功时 `make db-check` 输出 `"status": "ok"`，并显示数据库版本、UTF8 编码和 UTC 时区。迁移 `0001` 建立版本基线，`0002` 建立指数、证券、成分权重、每日观测、估值结果与运行记录表，`0003` 增加独立的数据商历史估值表，`0004` 增加个股历史 EPS/PE、来源核验及回填进度表。

## 指数数据与网页

启动并登录 Futu OpenD，确认行情权限可用，然后运行：

```bash
# 回填全部可获得的指数历史；收盘后可包含当天
uv run index-valuation backfill-index --index csi300
uv run index-valuation backfill-index --index csia500
uv run index-valuation backfill-index --index sse
uv run index-valuation backfill-index --index chinext
uv run index-valuation backfill-index --index star50
uv run index-valuation backfill-index --index hstech
uv run index-valuation backfill-index --index csidividend
uv run index-valuation backfill-index --index divlowvol
uv run index-valuation backfill-index --index sp500
uv run index-valuation backfill-index --index nasdaq100
uv run index-valuation backfill-index --index ustotal

# 打开本机网页：http://127.0.0.1:8000
uv run index-valuation serve

# 日常增量更新：从最后已存日期回退14天采集，随后重新生成指数序列
uv run index-valuation update-index --all

# 无需重新访问来源，只使用数据库重建指数序列及导出
uv run index-valuation build-index --index csi300
```

也可用 `make backfill-index`、`make daily`、`make web`。网页支持指数切换、1/3/5/10年及全部区间、指定日期回看、3/5/10年及全部历史统计窗口、固定倍数 band（保留8倍，各指数可配置）、历史平均 PE 虚线、滚动分位 band、PE 走势、逐日数据和 CSV 导出。接口为只读，仅绑定 `127.0.0.1`。读网页不需要 OpenD 在线，但 PostgreSQL 必须可访问。

页面并列显示“价格年化”和“分红再投资年化”，跟随图表所选区间及历史日期，分别显示实际起止日期。前者不含分红，后者使用独立采集的**税前全收益指数**，不从当前股息率估算；缺少全收益历史时显示原因。两者均与 PE 统计窗口独立，区间不同会明确提示。计算、来源及质量规则见[年化收益说明](docs/direct-index-valuation.md#年化收益)，实际覆盖和限制见[分红再投资接入记录](docs/total-return-integration-2026-10-19.md)。

每日定时更新支持 **Telegram 私聊通知**：任务结束后汇总本轮结果、真实数据日期、PE、历史分位和估值区间，失败与缺口单独提示；默认关闭，绑定收件人后启用。配置、测试和失败补发见 [Telegram 通知说明](docs/telegram-notifications.md)。

已有项目先运行 `uv run --locked alembic upgrade head` 创建独立的全收益数据表（迁移 `0007`）。为已有指数首次补充全收益历史，可运行以下命令；日后 `update-index` 和原有定时任务会一起更新，无需另设调度：

```bash
uv run --locked index-valuation backfill-returns --all
# 或只补充一个指数；支持 --start / --end
uv run --locked index-valuation backfill-returns --index csidividend
```

直达：[沪深300](http://127.0.0.1:8000/?index=csi300)、[中证A500](http://127.0.0.1:8000/?index=csia500)、[上证指数](http://127.0.0.1:8000/?index=sse)、[创业板指](http://127.0.0.1:8000/?index=chinext)、[科创50](http://127.0.0.1:8000/?index=star50)、[恒生科技](http://127.0.0.1:8000/?index=hstech)、[中证红利](http://127.0.0.1:8000/?index=csidividend)、[红利低波](http://127.0.0.1:8000/?index=divlowvol)。切换后刷新会保留所选指数。实际 PE 历史少于所选统计年限时，页面明确提示并显示实际样本起止日期。

沪深300数据覆盖 **2002-01-04 至 2026-10-13 的6,007个交易日**。富途 PE / PB / PS 各5,286条，隐含 EPS 5,286条，始于2005-01-04。2005-04-08之前标记为回溯数据，2002—2004年没有 PE 的日期保留点位、估值为空。[采集报告](docs/index-web-2026-10-13.md)

中证A500（`SH.000510`）点位覆盖 **2004-12-31 至 2026-10-13 的5,287个交易日**；富途 PE / PB / PS 及隐含 EPS 各495条，始于正式发布日2024-09-23。发布前只展示可用回溯点位；不借用沪深300或中证 PE 填充。已纳入 `update-index --all` 的每日更新。[接入报告](docs/csia500-index-2026-10-13.md)

新接入的上证指数、创业板指、科创50、恒生科技使用同一流程；市场、独立参考源、供应商代码映射和 band 倍数均由配置决定。上证指数等成分数量可变的指数允许不指定固定成分数（迁移 `0006`）。恒生科技使用 HKD 原生指数点位，未进行人民币换算。[四指数接入与核验报告](docs/new-indices-2026-10-13.md)

中证红利接入**中证红利指数 000922**（富途 `SH.000922`），共100只成分，展示价格指数。正式发布日期为2008-05-26，基日为2004-12-31；发布前数据标为回溯。点位和 PE 采用富途 OpenD，中证官方点位及滚动 PE 单独对照，BaoStock、东方财富提供价格核验及补源。复用现有估值带、均值和分位计算，并自动纳入每日 A 股更新。[中证红利接入记录](docs/csidividend-index-2026-10-19.md)

红利低波接入**中证红利低波动指数 H30269**，共50只成分。采用中证官方日度滚动 PE 和价格指数点位，东方财富点位、雪球基金 PE 用作对照。2026-10-14起使用官方优先方法 `csindex_index_implied_daily_v2`：第三方差异或缺失只作提示，官方数据继续参与 EPS、估值带及历史统计，缺失官方 PE 不替补。旧方法结果保留，新方法从已有完整历史重建，并已纳入每日更新。[红利低波接入与规则修订记录](docs/divlowvol-index-2026-10-13.md)

美股 ETF 对应指数已接入：[VOO → 标普500](http://127.0.0.1:8000/?index=sp500)、[QQQ → 纳斯达克100](http://127.0.0.1:8000/?index=nasdaq100)、[VTI → Morningstar US Total Market](http://127.0.0.1:8000/?index=ustotal)（原 CRSP US Total Market，2026年7月更名）。展示价格回报指数点位，未用 ETF 价格代替指数。

标普500和纳指100已改用 **富途 REST 的指数价格和指数 PE**；FRED 分发的标普官方点位、Nasdaq 官方点位只作价格核验，不再采集 Yahoo Finance。估值详情接口单独提供 PE/PB/PS，K 线内的零 PE 不使用。新方法为 `futorest_index_implied_daily_v1`，与旧 ETF 代理和雪球历史分开保存；固定 band 保留8倍，历史均值/分位使用至少252个先前有效日度 PE。

美国全市场的 VTI 基准暂未找到已验证的富途 REST 代码，**暂停新增采集**；页面明确标注暂停，并保留此前 Yahoo/Google 数据供只读查看。不会擅自换成 Wilshire 5000，也不会继续请求 Yahoo。

富途 REST 不依赖 OpenD 登录，首次需要单独的只读 OAuth 授权；授权页仅选择 `quote:read`。令牌保存在本机 macOS Keychain，到期前自动续期，不写入代码、日志或 `.env`。授权失效时再运行授权命令：

```bash
uv run --locked python scripts/auth_futu_rest.py
# 仅查看是否已配置及到期时间，不打印令牌
uv run --locked python scripts/auth_futu_rest.py --status
# 验证自动续期能力
uv run --locked python scripts/auth_futu_rest.py --refresh
uv run --locked index-valuation update-index --market US
```

已安装的北京时间09:00美股任务自动使用新配置；运行用户的 Keychain 需可访问。REST 价格缺失时不以其他源替补；缺少同日官方价格对照时仍保留价格和 PE，EPS/band 留空。PE 的盈利期间和加权细节未完整核实，页面称“指数 PE”和“隐含 EPS”。实际历史范围、缺口与验证见 [富途 REST 接入记录](docs/futu-rest-integration-2026-10-16.md)；旧 [Yahoo 代理记录](docs/yahoo-etf-proxy-2026-10-15.md)、[VTI 记录](docs/vti-proxy-2026-10-16.md)保留查阅。

直接指数结果存入独立 `index_series` 表（迁移 `ec157f69d574`）；原始来源仍在 `daily_observations` 和 `source_valuations`。每日导出至 `data/exports/`；美股 REST 和旧 Yahoo 代理 CSV 文件名额外包含方法名，避免覆盖旧口径导出，具体路径见运行报告。来源原始响应保存在 `data/raw/`。`collected` 只表示采集/派生完成，**不表示 PE 的会计口径已独立核实**。

## 可选：成分股采集与指数重算

启动并登录 Futu OpenD，确认具有 A 股行情权限。默认连接 `127.0.0.1:11111`，可在 `.env` 修改 `FUTU_HOST` / `FUTU_PORT`。应用只创建行情连接，无需交易解锁或交易密码。

```bash
uv run index-valuation list-indices
uv run index-valuation check-sources --index csi300
uv run index-valuation sync-constituents --index csi300
uv run index-valuation run-daily --index csi300  # 当日收盘后执行
uv run index-valuation status --index csi300

# 用已入库数据重算某日，不将今天的财务数据回填到过去
uv run index-valuation calculate --index csi300 --date 2026-10-13
```

成分重算需另行满足权重和个股财务数据要求；当前权重适配器为中证，仅使用已验证支持的指数。四个新指数的直接估值已接入，不表示其成分重算也已完成。上述命令建议显式指定 `--index`；`make constituents`、`make sources`、`make components-daily` 的 `--all` 不会自动获得新指数的成分重算能力。

成分 CSV 和每次任务的 JSON 报告位于 `data/exports/`，原始响应位于 `data/raw/`。数据库按来源保留每只股票的每日价格与 EPS，重复运行不会重复插入同一天的数据。任务由 PostgreSQL 锁防止重叠运行。

返回码：`0` 为完成或非交易日跳过，`2` 为等待收盘、数据不完整、历史试跑 `partial`、核验阻止估值或任务失败，`1` 为配置等启动错误。`review` 表示有 1%～5% 的数据差异，必须结合报告阅读，不进入历史分位样本。

## 从正式发布日期试跑历史数据

```bash
# 默认起点取 indices.toml 的 inception_date，终点取昨天
uv run index-valuation backfill-history --index csi300

# 固定区间，方便重复执行和核对
uv run index-valuation backfill-history --index csi300 --start 2005-04-08 --end 2026-10-12
```

沪深300默认从正式发布日 **2005-04-08** 开始，基日 2004-12-31 不作为默认起点。[中证官方指数单张](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000300factsheet.pdf)

历史试跑会分页抓取富途未复权指数日线、获取富途指数 PE 历史，并分别与 BaoStock 日线、中证公布的指数点位及滚动 PE 比较。中证按年度查询，交易日完整性使用 BaoStock 日历检查；非交易日记录保留原始响应并隔离。每次生成含逐日数据、差异、缺口的 CSV 和 JSON 报告，允许重跑，数据库不会插入重复日期。

**`backfill-history` 只回填指数参考序列并检查缺口，不自动重建历史成分估值。** 数据商 PE 单独存入 `source_valuations`，不会变成自行计算的 `daily_valuations` 或进入其历史 band。富途 K 线中的 `pe_ratio` 没有明确的 TTM 承诺，不用于反算历史 EPS；缺少富途早期日线时，核验源仍以各自来源保存。个股 EPS/PE 使用下面的独立回填命令。

真实试跑结果见 [2026-10-13 历史试跑记录](docs/history-trial-2026-10-13.md)。要完成按历史成分逐日重算，还需要相应日期的成分和权重、成分股历史价格与可核实口径的历史 TTM EPS，以及独立核验。

## 回填个股 EPS TTM 与 PE

```bash
uv run index-valuation backfill-fundamentals --index csi300 --start 2005-04-08 --end 2026-10-12

# 可中断后用同一命令继续；已完成的股票直接复用
# 指定股票重新采集，适用于源端修订或故障恢复
uv run index-valuation backfill-fundamentals --index csi300 --start 2005-04-08 --end 2026-10-12 \
  --symbols SH.600519 SH.601318 --refresh

# 有该日或更早的有效权重后，使用已核验的个股历史数据计算
uv run index-valuation calculate --index csi300 --date 2026-10-12 --historical-fundamentals
```

个股 PE 取富途 `get_valuation_detail` 的公司估值序列。富途公司估值说明将 PE 定义为 TTM，EPS 按同日未复权价格 / PE 推导；它是供应商估值隐含的盈利，不是重新计算的财报基本每股收益。[富途公司估值口径](https://support.futunn.com/topic497?from_platform=1&lang=zh-cn)

沪深300已配置 `historical_price_source="eastmoney"`，使用经用户授权的东方财富未复权历史价格补充富途日线额度与年限限制。富途仍为 PE 主源，BaoStock 独立核验价格、PE 和 EPS；不会将 BaoStock 改名为富途填缺。其他指数默认使用富途历史价格，可复用同一实现。

采集范围是该指数**已入库权重快照中出现过的证券集合**，按各自上市日期检查完整性。这不等于历年完整成分名单；取得更多历史权重快照后，新增证券会自动纳入。每只股票事务提交并保存进度，数据可跨指数共享。原始价格、富途 PE、推导 EPS 分开保存来源和文件路径；每股逐日 CSV 与汇总 JSON 位于 `data/exports/fundamentals/`。

`daily_fundamentals.status` 区分 `verified`、`review`（差异1%～5%）、`blocked`（差异超过5%）、`unverified`（缺核验源）、`missing_price` 和 `missing_pe`。有争议的候选值仍可查询审计，不能直接当作通过核验的数据。停牌缺价、零 PE 和上市初期缺失保留为空，不前向填充。`partial` 报告列出具体股票、日期缺口与失败原因；相同区间的重跑不会重复插入。

历史重算与每日快照使用独立方法版本，历史分位样本也分别累计。仍需真实历史权重和全部成分数据；只有当前300只股票的长序列，不能据此重建2005年以来的沪深300估值。

本轮实际回填了300只股票的1,091,805条隐含 EPS、1,091,975条 PE，质量状态仍为 `partial`。覆盖率、停牌缺口、最新日三方核验及指数重算结果见 [2026-10-13 EPS / PE 回填记录](docs/fundamentals-backfill-2026-10-13.md)。

## 每日自动更新

网页的[定时任务](http://127.0.0.1:8000/#scheduled-tasks)集中展示5项投资任务：计划时间、是否启用、下次计划、最近执行、数据日期和缺口。可展开查看执行结果原文；页面每分钟刷新状态，也可手动点击“刷新状态”。状态读取不会启动采集或写入表格。接口为 `/api/scheduled-tasks`，排队、执行结束和已回读验证分别展示，规则见[定时任务页面说明](docs/scheduled-tasks-dashboard.md)。

macOS 安装用户级任务：

```bash
make schedule  # 先生成可检查的 plist
uv run --locked python scripts/install_schedule.py --install
launchctl print gui/$(id -u)/cn.index-valuation.daily.asia
launchctl print gui/$(id -u)/cn.index-valuation.daily.us
```

北京时间**每天17:00更新A股和港股，每天09:00更新美股**，分别执行 `update-index --market CN HK` 和 `update-index --market US`。周末和节假日也运行，各指数按自身时区、收盘时间和交易日历选择数据，不生成非交易日记录。09:00覆盖美国夏令时和冬令时收盘。新增指数按配置中的市场自动归入任务。

安装器加载两个新任务后，停用旧的 `cn.index-valuation.daily` 全市场任务，并将旧 plist 备份到 `data/scheduler/backups/`，避免旧时段重复执行。调度使用系统时区，安装器要求系统处于 UTC+8；机器需开机、用户已登录，PostgreSQL 与已登录的 OpenD 可访问。

每次任务最长一小时，日志分别为 `logs/daily.asia.stdout.log` / `logs/daily.asia.stderr.log` 和 `logs/daily.us.stdout.log` / `logs/daily.us.stderr.log`，详细状态保存在 `pipeline_runs`。增量从最后已存日期回退14个自然日，可补采漏跑期间的数据；来源未发布或核验不足时保留缺口，供应商更早的修订使用 `backfill-index`。手动更新全部指数仍可使用 `make daily`。

停用两个已加载的任务（再次登录会重新加载保留的 plist）：

```bash
launchctl bootout gui/$(id -u)/cn.index-valuation.daily.asia
launchctl bootout gui/$(id -u)/cn.index-valuation.daily.us
```

Linux 可将 cron / systemd 调度时区设为 `Asia/Shanghai`，每天17:00调用项目 `.venv/bin/python scripts/run_scheduled.py --market CN HK`，每天09:00调用 `.venv/bin/python scripts/run_scheduled.py --market US`。

Google Sheets 的 `Index ERP` 表另有每天北京时间08:00的独立任务，通过已有 Codex 会话的 Google Drive 连接器更新指数盈利收益率和中美国债收益率。安装、日志和验证见 [表格定时同步说明](docs/google-sheets-erp-schedule.md)。

同一文件的 `Portforlio` 持仓表每天09:00同步美股价格、17:00同步中港及基金价格，均通过 Google Drive 连接器读写；原2026-10-16单次任务已改为每日任务。配置与运行记录见 [持仓报价定时同步](docs/portfolio-price-schedule.md)。

## 日常开发

```bash
make help                       # 查看常用命令
make check                      # 静态检查和格式检查
make test                       # 无需数据库的配置测试
make test-db                    # 真实 PostgreSQL 集成测试，需独立 TEST_DATABASE_URL
make db-shell                   # 使用运行账号进入本机 psql，需已安装客户端
make db-logs                    # 查看 ../local-infra 的共享 PostgreSQL 日志
make db-down                    # 仅提示基础设施维护位置，不停止服务
make db-up                      # 等待共享 PostgreSQL 就绪，不启动容器

uv run index-valuation --help
uv run python -m index_valuation check-db
```

集成测试检查迁移版本、数值精度、重复运行、多指数共享、校验失败和任务锁。必须单独配置 `TEST_DATABASE_URL`，数据库名以 `_test` 结尾（例如 `index_valuation_test`），使用仅能访问该测试库的专用账号，建议运行在独立临时 PG 容器。测试会在该库自动执行 Alembic 迁移，并为流程测试创建、清理临时 schema。测试连接绝不回退到正式的 `POSTGRES_*` 或 `MIGRATION_DATABASE_URL`；带可覆盖目标数据库的 URL query 参数也会被拒绝。

`make test-db` 缺少测试连接会直接报错；直接执行 `uv run pytest` 时，未配置测试连接的集成测试会跳过，其余测试照常执行。不要给正式运行账号增加建删 schema 的权限来通过测试。

添加依赖用 `uv add <package>`，开发依赖用 `uv add --dev <package>`，并一起提交 `pyproject.toml` 和 `uv.lock`。编辑器选择 `.venv/bin/python` 作为解释器。uv 缓存放在项目 `.cache/uv`，便于隔离环境。

## 配置与迁移

`.env.example` 是配置模板。`.env`、虚拟环境、缓存、本地数据和日志已被 Git 忽略。环境变量优先于 `.env`。

- 运行应用、网页、采集和 `make db-shell` 使用 `POSTGRES_*`，连接 URL 按字段构造，支持密码特殊字符。
- Alembic 单独读取 `MIGRATION_DATABASE_URL`，未配置时明确失败，不回退到运行账号。URL 使用 `postgresql+psycopg://`；密码中的特殊字符需要百分号编码。运行应用的配置类不加载该迁移字段。
- `TEST_DATABASE_URL` 只用于独立测试库，不能指向正式数据库。

共享实例上的运行账号应仅拥有本库的连接、schema 使用以及业务表和序列的必要权限，不具备超级用户、建库、建账号或迁移权限。对象归 `index_valuation_owner` 所有；基础设施应为该 owner 设置默认权限，确保新迁移创建的业务表和序列可被运行账号使用。

数据库生命周期和账号调整在 `../local-infra` 完成；更改 `.env` 不会自动修改数据库或密码。`make db-up` 只检查连接，`make db-down` 只提示维护位置，二者均不启停共享 PG。`INFRA_DIR` 可覆盖 Makefile 中的基础设施路径。备份由 infra 统一执行，另外还需备份本项目 `data/raw/` 和 `data/exports/`。

`compose.yaml` 的旧单库服务仅作为 `legacy` profile 历史模板保留，日常不再使用。迁移验证完成后，旧容器、数据卷、迁移备份、原配置副本和回退镜像均已清理，不能直接恢复旧实例。后续数据库恢复应按 `../local-infra` 的日常备份恢复流程重建，并先停止写入、评估备份时间之后的数据。不要用当前共享连接配置直接启动历史模板。

模型集中在 `models.py`，已由 `migrations/env.py` 导入。新增模型后生成并检查迁移：

```bash
uv run alembic revision --autogenerate -m "add index constituents"
uv run alembic upgrade head
uv run alembic current
```

目录结构：

```text
src/index_valuation/   领域记录、来源 Adapter、校验、估值、存储与任务编排
migrations/            数据库版本迁移
scripts/               开发环境辅助脚本
tests/                 配置测试和 PostgreSQL 集成测试
compose.yaml           旧单库服务的 legacy 历史模板，原实例已清理
pyproject.toml         依赖和开发工具配置
uv.lock                依赖锁文件
indices.toml           指数配置，新增同类指数的入口
docs/valuation-method.md 数据来源、EPS 与权重口径、计算规则
```

历史 band 默认需要至少252条当日之前的有效观测；数据不足会明确显示数量，固定 PE 倍数参考点位可在首个完整交易日计算。原始财报 EPS 和历史权重重建可在现有成分分析模块上继续扩展。

开发环境配置参考：[uv 项目与锁文件](https://docs.astral.sh/uv/guides/projects/)、[PostgreSQL 官方镜像](https://hub.docker.com/_/postgres)、[Alembic 迁移教程](https://alembic.sqlalchemy.org/en/latest/tutorial.html)。
