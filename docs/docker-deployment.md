# Mac 上的 Docker 网页部署

第一阶段仅将 FastAPI、静态网页及只读查询迁入 Docker。共享 PostgreSQL 由
`../local-infra` 管理；指数采集、OpenD、富途 REST Keychain、Telegram、ERP、政策日报和持仓表任务
继续在 Mac 上运行。网页访问地址仍为 `http://127.0.0.1:8000`。

## 运行结构

| 入口 | 位置与职责 |
| --- | --- |
| `compose.yaml` 的 `web` | 网页、CSV、估值与任务状态只读查询 |
| `local-infra` 的 PostgreSQL | 应用专属库和运行/迁移账号，独立备份 |
| Mac 09:00 / 17:00 的现有 LaunchAgent | 美股 / 中港采集、派生重建和 Telegram |
| `cn.index-valuation.policy-daily` | Mac 每天16:00检索、总结中国政策并推送 Telegram |
| `cn.index-valuation.task-summary` | Mac 每天18:00将六项业务任务的执行结论汇总发送至 Telegram |
| `cn.index-valuation.schedule-status` | 每 60 秒读取六项任务的 launchd 元数据，供网页展示 |

容器通过专用内部网络 `local-infra-index-db`，以 `shared-postgres:5432` 访问 PG。
宿主采集仍使用 `.env` 的 `127.0.0.1:15432`。本项目 Compose 仅引用 external 网络，
网络和 PostgreSQL 的声明归 `local-infra`。不要将应用接入其他项目的数据库网络。

首次迁移在线增加了数据库网络连接，没有重启共享 PG。其 Compose 配置也已同步；
今后 infra 执行配置协调可能重建 PG，应按共享实例维护流程处理三个应用的连接恢复。

## 首次部署与配置

1. 确认共享 PG、运行账号及 `local-infra-index-db` 网络已准备好。
   新机器由 infra 正常启动创建该网络；已有共享实例调整网络属于基础设施维护。
2. 本机 `.env` 保留原采集配置。Compose 只选取 `POSTGRES_DB`、`POSTGRES_USER`、
   `POSTGRES_PASSWORD` 和连接超时传给网页，并覆盖为内部网络地址。
   **不使用 `env_file: .env` 把整个文件注入容器。**
3. 用 `id -u` 和 `id -g` 核对 `.env` 的 `APP_UID` / `APP_GID`，默认 `501:20`。
   持仓回执是私有目录，容器用户必须能读取它；保持原目录权限。
4. 安装只读状态导出，先检查生成的 plist：

   ```bash
   uv run --locked python scripts/export_schedule_status.py --generate
   # 检查 data/scheduler/cn.index-valuation.schedule-status.plist
   uv run --locked python scripts/export_schedule_status.py --install
   ```

5. 确认以下只读挂载目录存在；全新环境可创建空目录，旧环境保留原内容：

   ```bash
   mkdir -p data/scheduler/host-status data/scheduler/index-runs \
     data/scheduler/erp-sheet data/scheduler/policy-daily data/scheduled/portfolio-daily logs
   ```

6. 首次先在其他端口验证：

   ```bash
   WEB_PORT=18000 docker compose up -d --build --wait web
   curl --fail http://127.0.0.1:18000/readyz
   ```

   与原网页核对所有指数后，停止已确认归属本项目的宿主网页进程，再执行 `make web-up`
   切换到 8000。不要通过端口号盲目结束未知进程。首次切换不改变采集或通知计划。

容器内监听 `0.0.0.0:8000`，宿主只发布 `127.0.0.1:${WEB_PORT:-8000}`。
镜像内无 `.env`、交易/行情凭据、通知 token 或 Mac 虚拟环境；构建上下文使用白名单。
镜像以非 root 运行、根文件系统只读，只有临时目录可写；日志最多保留三个 10 MB 文件。
Python 基础镜像固定 digest，依赖由 `uv sync --locked --no-dev --no-editable` 安装。
默认镜像地址使用 Google 的 Docker Hub 缓存镜像站，内容由 digest 固定。

## 更新、状态与健康检查

```bash
make web-up                  # 重建镜像并等待就绪
make web-logs                # 最近网页日志
docker compose ps
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
make web-down               # 仅停止网页；共享 PG 和业务调度继续运行
```

`/healthz` 仅检查应用进程。`/readyz` 使用运行账号，在只读事务中核对镜像中的
Alembic head、数据库迁移版本、网页使用的表列及 SELECT 权限；失败返回 503。
它不扫描估值历史，不执行迁移。Docker 的 unhealthy 状态不等于自动重启；
修复数据库或配置后检查恢复，进程退出才按 restart 策略重启。

代码、静态页面、研究资料、`indices.toml`、迁移目录均随镜像发布；修改后重建。
每日采集仍写共享库，网页实时读取，正常数据更新不需要重启。新表结构通过宿主
`make migrate` 使用 owner 应用，owner 凭据不进入网页容器。

宿主每分钟原子替换 `data/scheduler/host-status/status.json`，容器挂载其父目录，
因此能看到后续替换。快照仅含调度元数据；执行回执和数据日期分别从只读目录、
数据库读取。快照超过 180 秒、来自未来或身份无效时显示未知，保留已有执行证据。
较新的执行回执优先于旧采样，不把任务刚开始/结束误报为旧状态。

```bash
uv run --locked python scripts/export_schedule_status.py  # 只读导出一次
launchctl print "gui/$(id -u)/cn.index-valuation.schedule-status"
```

Mac 需要开机、用户登录，Docker Desktop 需要运行。休眠或 Docker 停止会影响网页；
宿主采集的 OpenD / Keychain 前提仍适用。本次没有迁移或新增业务调度器。

## 数据保存与验证

网页仅挂载 `host-status`、`index-runs`、`erp-sheet`、`policy-daily`、`portfolio-daily` 和 `logs`。
政策日报目录以只读方式挂载，API只读取执行回执和同日状态，不公开原始模型日志。
CSV 从数据库生成，网页无需挂载全部 `data/raw` / `data/exports` 或通知目录。
重建/移除网页容器不会删除宿主数据或共享 PG。

PG 备份由 infra 管理。应用的 `data/`（含原始响应、导出、任务和通知回执）、
必要凭据另行备份；通知回执丢失会失去原批次的去重证据。
本次容器化未把这些文件移入匿名卷，也未新增异机备份服务。

切换前后可执行只读接口对比；保存的基准应在没有更新任务运行时生成：

```bash
uv run --locked python scripts/check_docker_web.py snapshot --url http://127.0.0.1:8000
uv run --locked python scripts/check_docker_web.py compare --url http://127.0.0.1:18000
WEB_BASE_URL=http://127.0.0.1:18000 PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/playwright" \
  uv run --locked python scripts/check_web.py
WEB_BASE_URL=http://127.0.0.1:18000 PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/playwright" \
  uv run --locked python scripts/check_scheduled_tasks_web.py
```

对比覆盖所有指数 dashboard、CSV、市场汇总、代表日期回看和六项任务。
检查报告保存在 `data/exports/docker-migration/`。运行中的任务会产生真实回执差异，
应核实原因后重取基准，不应把业务时间或数据差异全部忽略。
数据库集成测试始终使用独立 `_test` 库，不向共享正式库创建测试表。

## 回退与下一阶段

应用回退仍连接当前共享 PG：

```bash
make web-down
uv run --locked index-valuation serve   # 宿主前台运行
```

也可用已经验证的旧镜像：指定 `APP_IMAGE_TAG` 并运行
`docker compose up -d --no-build --wait web`。先检查旧镜像与当前 schema 是否兼容。
停止网页不会停用宿主采集；状态导出任务可保留。如不再使用，执行
`uv run --locked python scripts/export_schedule_status.py --uninstall`，它只卸载自己的状态任务。

旧独立 PG 和旧卷已经删除；数据库恢复按 `local-infra` 的日常备份恢复流程处理，
不能通过旧 `legacy` profile 或迁移前快照回退数据库。

下一阶段的指数任务容器化须先完成：可持久续期的富途 REST 凭据存储、
容器到 Mac OpenD 的连通性、任务中断恢复及唯一调度入口。
静态 access token 无法代替持续授权，退出码 2 也不能当作无限重启条件。
