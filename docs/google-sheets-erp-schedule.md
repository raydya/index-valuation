# Google Sheets 指数风险溢价表定时同步

每天北京时间 08:00（含周末），独立的 macOS LaunchAgent 将任务提交到用户已有的
Codex 会话。会话使用 Google Drive 连接器更新 Portfolio 文件的 `Index ERP`
（sheetId `1665776241`），并向用户报告实际数据日期、失败项和回读验证结果。
指数盈利收益率按 PE 的倒数计算；国债为中美 10 年期。具体数据来源、口径和写入边界见
[每日执行指令](erp-sheet-update-task.md)。Google 表格读写仅使用连接器，不使用 `gws`。

本任务与本项目每天 17:00 / 09:00 更新本地指数数据库的任务独立；也不依赖 PostgreSQL
或 OpenD。运行需机器开机、用户登录、系统时区 UTC+8，Codex 会话可用，Google Drive
连接器授权和网络正常。建议保持 Codex 运行。系统睡眠、关机或会话繁忙可能延后执行，
不能保证 08:00 整完成更新。

## 安装与检查

下面使用本次已有会话 UUID；更换会话时必须先确认新会话可使用 Google Drive 连接器。
先生成检查，再安装。重复安装同一标签会替换配置，不创建第二份任务。

```bash
.venv/bin/python scripts/install_erp_schedule.py \
  --codex /opt/homebrew/bin/codex --thread 01a13ebd-dc5d-7192-8d98-933910a4362d
plutil -p data/scheduler/cn.index-valuation.erp-sheet.plist
.venv/bin/python scripts/install_erp_schedule.py \
  --codex /opt/homebrew/bin/codex --thread 01a13ebd-dc5d-7192-8d98-933910a4362d --install
launchctl print "gui/$(id -u)/cn.index-valuation.erp-sheet"
```

通过相同入口提交只读连接器验证，不改表，也不占用当天正式更新的提交记录：

```bash
.venv/bin/python scripts/run_erp_sync.py \
  --codex /opt/homebrew/bin/codex --thread 01a13ebd-dc5d-7192-8d98-933910a4362d --probe
```

调度器只调用 `codex queue`，不操作表格或创建新会话，也不改变 Codex 的沙箱或审批设置。
成功排队不代表表格更新成功：排队回执保存在 `data/scheduler/erp-sheet/last-queued.json`，
状态仅为 `queued`；执行与回读结果在原 Codex 会话中查看。两级结果应分别核对。
同一北京时间日期、同一会话成功提交后不会再次提交；提交失败不写成功记录，超时须先检查
会话是否已收到任务再决定是否重试。自动触发每日一次，不循环重试。

输出日志为 `logs/erp-sheet.stdout.log`、`logs/erp-sheet.stderr.log`。本任务不会自动
重连过期的 Google 授权；连接器不可用时报告失败，保留表格原值和日期。

## 停用

```bash
launchctl bootout "gui/$(id -u)/cn.index-valuation.erp-sheet"
```

如果不再需要开机登录后加载，将对应 `~/Library/LaunchAgents/cn.index-valuation.erp-sheet.plist`
移出 LaunchAgents 目录。保留项目脚本和日志不会自行运行。
