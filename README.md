# 指数估值系统

使用 Python、PostgreSQL 和本地网页构建指数估值应用：采集指数每日点位、PE / PB / PS，推导隐含 EPS，展示固定倍数和历史分位估值带。

主流程采用指数整体估值，不依赖成分股数据。内地、香港指数以 Futu OpenD 为主；美股指数使用明确配置的价格、PE 来源。支持三个市场各自的交易日历与时区。原有成分股采集与重算流程保留为独立分析能力。指数通过 `indices.toml` 配置，新增同类指数可复用计算与网页。

**指数隐含 EPS = 同日收盘点位 / 配置主源的指数 PE。** 单位为指数点；供应商盈利、加权和亏损处理尚未全部独立核实。不同主源使用独立方法版本，参考 PE 不混入主源历史，缺失值不填充。详见 [直接指数估值说明](docs/direct-index-valuation.md)。

[各指数恐慌区间历史检验](docs/panic-ranges-2026-10-14.md)比较低 PE 分位、大幅回撤及其联合条件之后的价格回报，包含来源口径、短历史、重叠样本和继续下跌风险检查。通用入口 `scripts/analyze_panic_ranges.py` 支持对全部配置指数只读导出快照并复跑；完整方法和命令见报告。

每个指数页面的“PE 恐慌低点参考”保存研究时的观察上限、成熟信号 PE 范围、非重叠窗口及后续价格回报，并提供全部指数对照和 JSON 下载。观察线是注明日期的固定研究记录，日常行情更新不会重算；短历史或缺 PE 时不设线。历史回看早于研究日期时不计算与该线的差距。[记录维护与复用](docs/panic-reference.md)。

## 技术栈

| 用途 | 工具 |
| --- | --- |
| 运行环境 | Python 3.12、uv、项目内 `.venv` |
| 数据库 | PostgreSQL 17，Docker Compose 管理 |
| 数据访问 | SQLAlchemy 2.0、Psycopg 3 |
| 结构迁移 | Alembic |
| 配置 | pydantic-settings，读取 `.env` 与环境变量 |
| 网页 | FastAPI、Uvicorn、原生 JavaScript / SVG，无 CDN 依赖 |
| 开发检查 | Ruff、pytest |

依赖的确切版本保存在 `uv.lock`；日常安装使用 `uv sync --locked`。Python 小版本由 `.python-version` 固定，补丁版本可更新。PostgreSQL 镜像固定 17 主版本，允许更新该版本内的补丁。

## 首次启动

前置条件：安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和可用的 Docker Engine / Docker Desktop（含 Compose v2）。Docker 服务需处于运行状态。生成配置的脚本兼容系统 Python 3.9+；应用使用 Python 3.12，缺失时 uv 会自动安装。

在项目根目录执行：

```bash
make setup
```

该命令会依次：

1. 创建 `.env`，生成随机数据库密码；已有文件不会被覆盖。
2. 根据锁文件创建 `.venv` 并安装应用和开发依赖。
3. 启动 PostgreSQL 并等待 Python 能够连接。
4. 应用 Alembic 迁移。
5. 输出数据库连接信息，密码不会出现在输出中。

默认连接地址为 `127.0.0.1:5433`，数据库和用户名均为 `index_valuation`。端口仅绑定本机，避免占用常见的 5432 端口。数据保存在 Compose 的 `postgres_data` 命名卷中。

可以分步执行：

```bash
make env
make install
make db-up
make migrate
make db-check
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

顶部“年化收益”按图表所选区间的首末可用点位计算复合年化涨幅，随区间和历史日期变化，显示实际起止日期。该值**不含分红**，与 PE 统计窗口独立；计算公式、短历史和价格质量处理见[年化收益说明](docs/direct-index-valuation.md#年化收益)。

直达：[沪深300](http://127.0.0.1:8000/?index=csi300)、[中证A500](http://127.0.0.1:8000/?index=csia500)、[上证指数](http://127.0.0.1:8000/?index=sse)、[创业板指](http://127.0.0.1:8000/?index=chinext)、[科创50](http://127.0.0.1:8000/?index=star50)、[恒生科技](http://127.0.0.1:8000/?index=hstech)、[红利低波](http://127.0.0.1:8000/?index=divlowvol)。切换后刷新会保留所选指数。实际 PE 历史少于所选统计年限时，页面明确提示并显示实际样本起止日期。

沪深300数据覆盖 **2002-01-04 至 2026-10-13 的6,007个交易日**。富途 PE / PB / PS 各5,286条，隐含 EPS 5,286条，始于2005-01-04。2005-04-08之前标记为回溯数据，2002—2004年没有 PE 的日期保留点位、估值为空。[采集报告](docs/index-web-2026-10-13.md)

中证A500（`SH.000510`）点位覆盖 **2004-12-31 至 2026-10-13 的5,287个交易日**；富途 PE / PB / PS 及隐含 EPS 各495条，始于正式发布日2024-09-23。发布前只展示可用回溯点位；不借用沪深300或中证 PE 填充。已纳入 `update-index --all` 的每日更新。[接入报告](docs/csia500-index-2026-10-13.md)

新接入的上证指数、创业板指、科创50、恒生科技使用同一流程；市场、独立参考源、供应商代码映射和 band 倍数均由配置决定。上证指数等成分数量可变的指数允许不指定固定成分数（迁移 `0006`）。恒生科技使用 HKD 原生指数点位，未进行人民币换算。[四指数接入与核验报告](docs/new-indices-2026-10-13.md)

红利低波接入**中证红利低波动指数 H30269**，共50只成分。采用中证官方日度滚动 PE 和价格指数点位，东方财富点位、雪球基金 PE 用作对照。2026-10-14起使用官方优先方法 `csindex_index_implied_daily_v2`：第三方差异或缺失只作提示，官方数据继续参与 EPS、估值带及历史统计，缺失官方 PE 不替补。旧方法结果保留，新方法从已有完整历史重建，并已纳入每日更新。[红利低波接入与规则修订记录](docs/divlowvol-index-2026-10-13.md)

美股 ETF 对应指数已接入：[VOO → 标普500](http://127.0.0.1:8000/?index=sp500)、[QQQ → 纳斯达克100](http://127.0.0.1:8000/?index=nasdaq100)、[VTI → Morningstar US Total Market](http://127.0.0.1:8000/?index=ustotal)（原 CRSP US Total Market，2026年7月更名）。展示价格回报指数点位，未用 ETF 价格代替指数。

截至2026-10-13，三者分别保存17,520、10,337、3,906条价格。标普500和纳指100以雪球基金发布的 PE TTM 为主，分别有470、469条有效历史采样；每个已结束自然周最多计一个样本，最低52个周观测才显示均值和分位。2026-10-12的两条 PE 对照差异均超过5%，已标记并停用当天 EPS/band；可点击网页中的入口回看2026-10-08的参考 band。VTI 基准目前只有单源价格，PE/EPS/band 留空。[美股接入、数据来源和限制](docs/us-indices-2026-10-13.md)

直接指数结果存入独立 `index_series` 表（迁移 `ec157f69d574`）；原始来源仍在 `daily_observations` 和 `source_valuations`。每日导出 `data/exports/指数标识-index-bands-日期.csv`，来源原始响应保存在 `data/raw/`。`collected` 只表示采集/派生完成，**不表示富途指数 PE 的会计口径已独立核实**。

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

macOS 安装用户级任务：

```bash
make schedule  # 先生成可检查的 plist
uv run python scripts/install_schedule.py --install
launchctl print gui/$(id -u)/cn.index-valuation.daily
```

北京时间周一至周五21:00、22:00，以及周二至周六07:00、08:00运行全部配置指数，覆盖美国夏令时及冬令时收盘后的采集与重试。各指数根据自身时区选择已收盘日期。调度入口为 `update-index --all`，近14天重叠采集可补上近期漏跑日；只接收对应市场交易日历中的记录。调度使用系统时区，安装器要求系统处于 UTC+8。

机器需保持开机、登录，PostgreSQL 与已登录的 OpenD 可访问。历史接口缺失的数据不填充；供应商更早的历史修订可通过 `backfill-index` 全量刷新。每次任务最长一小时，日志为 `logs/daily.stdout.log` 与 `logs/daily.stderr.log`，详细状态同时保存在 `pipeline_runs`。直接流程不再每日抓取300只成分股，成分流程可单独运行。

停用当前用户任务：

```bash
launchctl bootout gui/$(id -u)/cn.index-valuation.daily
```

Linux 可通过 cron / systemd 每个工作日 21:00、22:00 调用项目 `.venv/bin/python scripts/run_scheduled.py`，时区设为 `Asia/Shanghai`。

## 日常开发

```bash
make help                       # 查看常用命令
make check                      # 静态检查和格式检查
make test                       # 无需数据库的配置测试
make test-db                    # 真实 PostgreSQL 集成测试，需先 make setup
make db-shell                   # 进入 psql
make db-logs                    # 查看数据库日志
make db-down                    # 停止并移除容器，保留数据卷
make db-up                      # 再次启动，复用原有数据

uv run index-valuation --help
uv run python -m index_valuation check-db
```

集成测试检查迁移版本、数值精度、重复运行、多指数共享、校验失败和任务锁。流程测试使用独立临时 schema，结束后只删除该临时 schema。直接执行 `uv run pytest` 会同时运行配置和数据库测试。

添加依赖用 `uv add <package>`，开发依赖用 `uv add --dev <package>`，并一起提交 `pyproject.toml` 和 `uv.lock`。编辑器选择 `.venv/bin/python` 作为解释器。uv 缓存放在项目 `.cache/uv`，便于隔离环境。

## 配置与迁移

`.env.example` 是配置模板。`.env`、虚拟环境、缓存、本地数据和日志已被 Git 忽略。环境变量优先于 `.env`；应用和迁移使用相同配置，连接 URL 由字段构造，支持密码中的特殊字符。

已有 PostgreSQL 服务也可直接使用：运行 `make env`、`make install`，将 `.env` 的 `POSTGRES_*` 设置为该服务的专用开发数据库，然后运行 `make migrate` 和 `make db-check`。

数据库初始化后，修改 `.env` 中的密码、用户名或数据库名不会自动修改卷内已有数据库。应先在数据库中进行相应变更，再同步配置。`docker compose down -v` 会删除数据卷，日常停止请使用 `make db-down`。

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
compose.yaml           本地 PostgreSQL 服务及持久化数据卷
pyproject.toml         依赖和开发工具配置
uv.lock                依赖锁文件
indices.toml           指数配置，新增同类指数的入口
docs/valuation-method.md 数据来源、EPS 与权重口径、计算规则
```

历史 band 默认需要至少252条当日之前的有效观测；数据不足会明确显示数量，固定 PE 倍数参考点位可在首个完整交易日计算。原始财报 EPS 和历史权重重建可在现有成分分析模块上继续扩展。

开发环境配置参考：[uv 项目与锁文件](https://docs.astral.sh/uv/guides/projects/)、[PostgreSQL 官方镜像](https://hub.docker.com/_/postgres)、[Alembic 迁移教程](https://alembic.sqlalchemy.org/en/latest/tutorial.html)。
