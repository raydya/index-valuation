# 项目协作约定

本项目使用 Python、PostgreSQL 和本地网页采集指数点位、PE，计算隐含 EPS 和估值 band。默认工作流是**指数整体估值**；成分股、历史权重及公司财务重算是独立能力，仅在任务明确涉及它们时使用。

## 获取数据与验证工具

- 默认使用 API、网络搜索、直接读取网页或 PDF 获取数据；本地网页交互验证使用项目已有的 Playwright WebKit。
- 只有用户明确指定通过 Google Chrome 获取数据或验证功能，才可在指定用途和范围内使用 Google Chrome。
- 如果 Google Chrome 是唯一可行方式，先说明必要原因以及其他方式为何不满足要求，等待用户明确授权；不得自行将“必须使用”视为授权。
- 用户拒绝后继续完成允许方式可完成的工作，不反复请求同一权限，不通过更换浏览器绕过拒绝；说明未完成的信息或验证及其影响。

## 工作入口与设计原则

- 环境和启动命令见 [README.md](README.md)，依赖以 [pyproject.toml](pyproject.toml) 和 `uv.lock` 为准。命令在项目根目录运行，使用项目 `.venv` 或 `uv run --locked`；首次配置按 README 执行 `make setup`，已有环境不重复初始化。
- 新增指数、调整来源、PE 口径或增量逻辑前，阅读 [直接指数估值说明](docs/direct-index-valuation.md)。处理海外指数、ETF 基准映射、稀疏 PE 或美股接口限制时，再阅读 [美股接入记录](docs/us-indices-2026-10-13.md)；历史报告中的权限、覆盖率和数值需以本次核验为准。
- 指数差异放在 [indices.toml](indices.toml)，来源差异放在 [providers](src/index_valuation/providers)。同源指数复用适配器，计算和网页复用通用实现，不复制一套按指数名称分支的流程。
- 配置结构和校验分别在 [domain.py](src/index_valuation/domain.py)、[registry.py](src/index_valuation/registry.py)；来源工厂在 [cli.py](src/index_valuation/cli.py)，采集编排在 [history.py](src/index_valuation/history.py)，派生与统计在 [index_series.py](src/index_valuation/index_series.py)。新增数据源时需检查这些接入点，不能只增加一个 TOML 源名称。
- 内地、香港指数继续以富途为主。用户已授权历史价格补源，以及美股使用其他 PE 主源并单独标注口径、交叉核验；在该范围内继续实施，无需重复询问已获授权的事项。缺少权限、供应商明确不支持和临时请求失败要分别记录。
- 富途未收录的中证指数已有官方 PE 主源能力（`valuation_source = "csindex"`）；红利低波的代码映射、实际覆盖和限制见[接入记录](docs/divlowvol-index-2026-10-13.md)。`CSI.<代码>` 是内部标识，须关闭富途报价并显式映射其他价格源；内地交易日历仍独立采集。不能将中证主源与自己交叉核验，也不能把雪球旧代码返回的历史冒充当前有效接口。

## 新增指数流程

1. **确认指数身份。** 查发布机构或 ETF 发行人的一手资料，记录正式名称、市场、原生币种、时区、正式发布日期、可获得的回溯起点和出处。用户给出 ETF 时，先确认跟踪基准；区分价格回报、净收益、全收益版本，并核查更名或更换基准的连续性。完成标准：各供应商代码均指向同一个已确认的指数版本。
2. **小范围验证来源。** 分别试取最近几个已收盘交易日的指数价格和 PE；验证证券身份、日期、字段含义、采样频率、权限和返回范围。优先用至少两个数据源交叉核验，注明是否共享同一上游。富途能列出证券代码不代表支持该证券的历史或估值接口。完成标准：知道每个来源实际提供什么、哪些字段缺失以及原因，保留试取响应。
3. **补充配置或适配器。** 在 `indices.toml` 新增唯一 key，参照同市场、同来源的现有配置设置代码映射、日期、`currency`、`timezone`、`close_time`、`futu_supported`、价格参考源、附加来源、`valuation_source` 和 band 参数。ETF 映射填写 `tracking_etfs`、`description`；成分数可变时不硬填固定数量。保留8倍 band。若现有适配器或市场日历不适用，先实现可复用能力，再接入配置。完成标准：注册表校验通过，主源确实被采集工厂启用，价格专用指数明确配置为无 PE。
4. **执行历史回填。** 先用短区间确认入库，再从已核实的 `history_start_date`（未配置则为 `inception_date`）执行 `backfill-index`。允许采集供应商提供的发布前回溯数据，保留 `backcast` 标记。完成标准：实际数据已入库，价格、PE、EPS 各自覆盖范围、隔离记录及来源失败有报告；明确区分“已取得全部可用数据”和“从发布日起无缺口”。
5. **接入网页和更新。** 配置会自动进入 `--all` 和网页选择列表。新增配置或修改后端后，重启本项目的网页服务，使启动时加载的配置生效。执行一次该指数的 `update-index`，核对与回填重合的数据不会重复插入，新增或修订记录有来源依据。完成标准：网页/API 可选择该指数，能查看已有数据和缺口，导出与数据库一致。
6. **验证并交付。** 配置变更核对身份映射、市场和适用来源；适配器或计算变更测试错误身份、日期/时区、未收盘数据、缺失值、分页或采样等实际风险。涉及存储时运行 PostgreSQL 集成测试，涉及展示时验证切换、日期回看、8倍及均值/分位 band、CSV 和手机布局。更新 README 的入口与 `docs/` 接入记录，说明来源、口径、历史范围、已知缺口及验证结果；不要把动态行情数值或测试总数写成本文件的长期约定。

常用命令如下；将 `index_key` 改为已经加入配置的 key，短区间试跑可为回填命令增加 `--start`、`--end`（ISO 日期，结束日必须已收盘）。

```bash
index_key=csi300
uv run --locked index-valuation list-indices
uv run --locked index-valuation backfill-index --index "$index_key"
uv run --locked index-valuation update-index --index "$index_key"
uv run --locked index-valuation status --index "$index_key"
```

**新增指数的完成标准：** 映射有出处，配置/适配器可复用，回填和增量实际运行过，网页与导出可用，数据缺口和校验结果明确。若仍只有价格或来源不可用，完成可实施的接入并准确报告限制，不伪造 PE、EPS 或 band。

## 每日增量数据流程

1. **检查运行条件。** 确认系统日期、PostgreSQL 和环境配置；使用富途的指数需 OpenD 已登录且行情可用。读取已有配置及运行状态，保持凭据在 `.env` 或环境变量中，不输出或提交凭据。
2. **确定范围并运行。** 日常全部指数使用 `update-index --all`，指定指数使用 `--index`。CLI 根据每个指数自身的时区及 `close_time` 确定截至日期，再用对应市场日历过滤交易日；不得用北京时间当天或简单工作日替代海外交易日。
3. **保留重叠窗口。** 常规增量从当前方法最后已存日期回退14个自然日，最低不早于配置起点；这是从最后已存日期回退，不是仅取“今天往前14天”，因此停机后的缺口也能补采。当前方法尚无结果时走完整起点；支持富途但一直没有 PE 的指数也保留完整回填起点，以便权限恢复后补齐。已知不支持富途的价格专用指数正常增量。
4. **采集、核验、重建。** 复用命令内部的任务锁、原始响应归档、来源分开存储和 upsert。`update-index` 自动重建该指数已入库的派生序列及导出，无需再手工插入 EPS 或重复调用 `build-index`。周末或源暂未更新时保持真实数据日期。
5. **检查结果而非只看退出码。** 阅读每个指数的报告，核对最新已收盘日、各源最后日期与行数、PE/EPS 覆盖、核验状态、隔离记录及 `latest_complete_date`。退出码0表示命令正常完成，仍要检查质量；2表示存在部分完成、阻塞或失败等需查看报告的状态，数据可能已经入库；1通常表示配置/执行异常，已完成指数可能仍已保存。`collected` 不表示财务口径已独立核实。
6. **定位后补采。** 临时失败后重跑该指数的 `update-index`；超过增量窗口的旧数据修订使用带 `--start`、`--end` 的 `backfill-index` 或完整回填。仅修改派生逻辑、输入已经齐备时使用 `build-index`。切换 PE 主源、基准或采样方法时检查方法版本与历史边界，不能用当前口径覆盖另一方法，也不能仅靠14天增量补齐新方法历史。
7. **核对发布结果。** 查看网页/API 与 CSV 的最新日期及值。正常增量不需重启网页；配置和后端代码变更需重启。若 `--all` 中途异常退出，核对每个配置 key 是否有本次运行记录，对未执行项逐一补跑。结束时报告已更新范围、剩余缺口和处理结果，不能把整个批次笼统称为成功。

```bash
# 日常主入口；make daily 等价
uv run --locked index-valuation update-index --all

# 查看单个指数的任务报告；这里沿用已设置的 index_key
uv run --locked index-valuation status --index "$index_key"

# 只重算已入库数据，不请求外部来源
uv run --locked index-valuation build-index --index "$index_key"
```

**增量完成标准：** 所选指数均有可追溯的执行结果；应有的新交易日已补入，或有明确的来源缺口说明；重叠日期无重复主键，网页、CSV 和报告的日期/数值一致。单源失败保留其他源及已入库数据，不通过删除历史后重跑来“修复”。

## 数据口径与质量边界

- 隐含 EPS = **同日指数收盘点位 / 配置主源 PE**，单位为指数点；band = 隐含 EPS × PE 倍数。数值计算使用 `Decimal`。ETF 价格、基金持仓 PE、预期 PE、CAPE 和全收益指数都不能直接替代这一组合；未充分核实盈利口径时称“隐含 EPS”。
- 每条来源记录保存证券身份、日期、来源、口径、观测时间及原始响应路径。日历外或身份错误的响应隔离并报告；缺失 PE/价格保持为空，不能前向填充、移到相邻日期或把缺失当零。发布日期前的回溯数据与正式发布后的数据分别标记。
- 数值差异阈值为≤1%、1%—5%、>5%，分别标记通过、警告、重大差异。单源价格仍标为未核验，差异状态和计算资格分开保存。中证官方主源方法优先采用官方点位，第三方价格缺失或差异不阻止使用有效官方点位；非官方点位仍须独立价格核验通过或警告才推导 EPS/band。数值接近不等于会计口径一致。
- 指数官方数据优先：`valuation_source = "csindex"` 使用 `csindex_index_implied_daily_v2`，第三方 PE 差异或缺少对照仅作提示，官方 PE 继续参与 EPS/band 及历史均值、分位；官方 PE 缺失时不以第三方替补。保留各源原值、来源、差异和旧方法结果，不把采用官方数据写成独立核验通过。美股替代 PE 仍在任一同日对照存在重大差异时设置 `pe_usable=false`，停用该日 EPS/band 及该 PE 的统计贡献。既有富途方法保留其独立口径与差异标记；变更历史政策须显式更新方法及说明。
- 均值和分位只使用同一方法的有效正 PE，排除当天及未来数据，样本阈值读取配置。富途和中证官方主源为日度采样；雪球作为主源时，统计取每个已结束自然周的最后有效值，排除当前周，避免每日快照使近期周重复加权。雪球仅作对照时不改变主源采样。其他采样频率须先定义对应方法，不能只降低样本阈值或伪造日度记录。
- 权限拒绝、验证码或付费限制按真实失败记录；可用来源继续工作。人工核验的公开参考快照须记录真实来源、日期、取数方式和证据，不能宣称已建立稳定的自动核验来源。美股源的日期编码、Yahoo 日线降采样和指数代码映射等特殊处理见美股接入记录与适配器测试。

## 日志、调度与验证入口

| 入口 | 用途 |
| --- | --- |
| `daily_observations` / `source_valuations` | 按来源保存的价格、估值输入 |
| `index_series` | 按指数、日期、方法保存的派生序列 |
| `pipeline_runs`、`status --index` | 任务状态与报告；检查摘要指向的详细采集报告 |
| `data/raw/` | 按内容哈希归档的来源响应 |
| `data/exports/` | 回填/派生报告、CSV、网页验证产物 |
| `logs/daily.stdout.log` / `logs/daily.stderr.log` | 定时任务输出与错误日志 |
| `/api/indices`、`/api/indices/{key}/dashboard`、`/api/indices/{key}/export.csv` | 本地网页的数据与导出接口 |

macOS 调度由 [scripts/install_schedule.py](scripts/install_schedule.py) 生成，入口 [scripts/run_scheduled.py](scripts/run_scheduled.py) 执行 `update-index --all`，单次最长一小时。当前时段覆盖北京时间周一至周五21/22点、周二至周六7/8点，以脚本为准；机器需开机、用户已登录、系统时区为 UTC+8，数据服务可访问。新增配置会自动进入下一次 `--all`，无需为每个指数创建定时任务。

安装或调整调度时先生成并检查 plist，再在用户已授权范围内安装；日常增量不重复安装：

```bash
make schedule
uv run --locked python scripts/install_schedule.py --install
launchctl print "gui/$(id -u)/cn.index-valuation.daily"
```

按修改范围运行必要验证；纯文档变更检查路径、命令和描述，无需触发历史采集或完整应用测试：

```bash
make check
make test
make test-db
node --check src/index_valuation/web/app.js
# 本地网页运行且 WebKit 已安装时
PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/playwright" uv run --locked python scripts/check_web.py
```

`run-daily` / `make components-daily`、`sync-constituents`、`check-sources` 及 `backfill-fundamentals` 属于成分股工作流；其数据源和权重要求与直接指数流程不同。仅在任务明确要求成分分析时，阅读 README 的成分章节并使用这些命令，不把它们作为新增海外指数或每日直接估值的前置步骤。
