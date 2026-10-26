# Google Sheets 指数风险溢价表定时同步

每天北京时间08:00（含周末），Mac LaunchAgent 启动独立的 `codex exec`，通过已有
Google Drive 连接器更新 Portfolio 文件的 `Index ERP`（sheetId `1665776241`）。
不再向需要有人恢复执行的旧会话排队，不要求 Codex 界面打开。Google 表格读写仍仅使用
连接器，不使用 Google CLI、Chrome 或自建 Google 凭据。

指数盈利收益率按PE倒数计算，国债使用中美10年期收益率。具体来源、口径、保留项和回读要求见
[每日执行指令](erp-sheet-update-task.md)。业务任务不依赖本地 PostgreSQL 或 OpenD。
机器需开机、用户登录、系统时区UTC+8，Codex登录、连接器授权和网络正常；睡眠可能延后执行。

## 安装与检查

先检查生成配置，再替换同名ERP任务。安装器保留旧配置备份，只修改自己的标签，不触动持仓、
指数或18:00汇总任务；正在执行时拒绝替换，安装不会立即执行。

```bash
.venv/bin/python scripts/install_erp_schedule.py --codex /opt/homebrew/bin/codex
plutil -p data/scheduler/cn.index-valuation.erp-sheet.plist
.venv/bin/python scripts/run_erp_sync.py --dry-run
.venv/bin/python scripts/install_erp_schedule.py --codex /opt/homebrew/bin/codex --install
launchctl print "gui/$(id -u)/cn.index-valuation.erp-sheet"
```

需要手动运行当天尚未执行的任务时：

```bash
launchctl kickstart "gui/$(id -u)/cn.index-valuation.erp-sheet"
```

`--dry-run` 不启动执行、不修改表格、不占用当天机会。正式执行每天最多一次：08:00前跳过，
同一天已有尝试则不自动重跑，包括失败和超时。全程文件锁避免并发，子进程也持有锁；
上次进程异常退出留下的运行记录，在确认锁已释放后记为中断，仍不自动重复写表。
若需人工恢复，先核对该次事件、业务回执和真实表格，不能删除状态文件后盲目重跑。

已授权的人工恢复使用独立 `run_id`、真实恢复开始/结束时间、`mode = manual_recovery`，
并记录 `execution_origin = authorized_manual_recovery` 与原执行的 `recovery_of_run_id`；
不伪装成原定时进程完成，`exit_code` 和 `process_exit_code` 保持空值。
先归档原 `last-execution.json` 和当日业务回执，保留 `runs/YYYY-MM-DD/` 内原失败状态与事件。
实际表格回读通过后，使用现有业务结果校验规则发布新的匹配回执和最新执行记录。
人工恢复只发布已核验的 `completed` / `partial` 终态；来源标识、执行身份或回读证据不匹配时，
网页和汇总显示未知，不采用原完成记录替代。仍有日期、口径或来源缺口时保持 `partial`。

## 执行、回执和异常

执行命令使用与持仓任务相同的 `codex --no-daemon exec --approve-for-me`，保留自动审批审查，
不关闭沙箱。提示从标准输入传入，不经shell解释。最长运行一小时，超时或中断会终止进程组。
正常退出只说明流程结束；最终输出须符合[结果结构](erp-result.schema.json)，再由宿主验证并保存。

| 路径 | 作用 |
| --- | --- |
| `data/scheduler/erp-sheet/last-execution.json` | 当前执行标识、开始/结束时间、进程和业务终态 |
| `data/scheduler/erp-sheet/runs/YYYY-MM-DD/state.json` | 当天执行与防重记录 |
| 同目录 `prompt.md` / `events.jsonl` / `stderr.log` / `result.json` | 执行指令、事件、错误及结构化最终输出 |
| `data/scheduler/erp-sheet/results/YYYY-MM-DD.json` | 匹配执行标识的已验证业务回执 |
| `logs/erp-sheet.stdout.log` / `logs/erp-sheet.stderr.log` | LaunchAgent 运行日志 |

`completed` 表示十个目标均提供已核验日期、没有未解决问题且已真实回读；`partial` 表示已回读，
但存在来源未更新、日期或口径未核实；无法读取、写入或回读时为 `failed`。进程退出0但缺少有效
业务回执也记为失败。退出码0/2/1分别对应完成/部分完成/失败；超时124，中断按信号记录。
不把失败写成“未修改表格”：写入可能已完成但回读/保存失败，必须核对真实表格。

网页及每天18:00 Telegram汇总读取同一回执。新执行结果优先，缺失或损坏时不以旧完成结果代替；
旧 `last-queued.json`、`last-attempt.json` 保留供历史查询，不再作为新任务的执行入口。
修改状态解析代码后需要 `make web-up` 重建网页镜像；日常结果更新无需重建。

## 从旧队列方式迁移

2026-10-20已确认旧任务仅调用 `codex queue`，10月17日至20日四条消息没有启动执行。
迁移时通过 Codex App Server 的 `thread/queue/list` 核对目标会话与ERP身份，备份后按精确消息ID
调用 `thread/queue/delete`，最终确认队列为空；未修改内部数据库或删除会话。
审计保存在 `data/scheduler/erp-sheet/diagnostics/queue-retired-2026-10-20.json`。
之后只执行当天更新，不把积压四条逐一重放，以免旧任务重复覆盖表格。

其他机器迁移前也需核对旧队列，不能只换定时器。队列接口以本机版本支持的schema为准。
旧的 `--thread` 和 `--probe` 参数已移除；记录脚本仅为历史兼容/受控人工记录入口，常规任务由宿主
自动生成执行身份和时间，不依赖模型另调用一次脚本。

## 停用

```bash
launchctl bootout "gui/$(id -u)/cn.index-valuation.erp-sheet"
```

停用前核对任务是否运行。若正在运行，此操作会中断执行，须检查实际表格及回执；
不再需要登录后加载时，将对应plist移出 `~/Library/LaunchAgents/`。历史结果应保留。
