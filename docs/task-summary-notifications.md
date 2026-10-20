# 每天18:00的五任务汇总通知

北京时间每天18:00，独立的 Mac LaunchAgent 汇总以下五项任务，通过已经绑定的
Telegram Bot 私聊推送：ERP 表格、美股持仓、中港及基金持仓、美股指数估值、中港指数估值。
包括周末及节假日，数据仍显示实际日期；排队、执行中、失败、部分完成和缺少回执会分别说明。

此汇总读取已有执行证据，不重新采集行情或更新表格。原有08:00 ERP、09:00/17:00业务任务、
指数批次结束后的 Telegram 通知继续按原计划运行。网页容器无需运行，汇总由 Mac 执行；
机器需开机、用户登录、系统时区为 Asia/Shanghai（UTC+8），Telegram 网络或代理须可用。
电脑睡眠可能延后执行，不能保证离线时准点送达。

## 安装及核对

复用项目 `.env` 中已绑定的 `TELEGRAM_*` 配置；不把 token、会话 ID 或代理密码写进 plist。
先生成并检查独立任务：

```bash
uv run --locked python scripts/install_task_summary_schedule.py --generate
plutil -p data/scheduler/cn.index-valuation.task-summary.plist
uv run --locked python scripts/notify_task_summary.py preview
uv run --locked python scripts/install_task_summary_schedule.py --install
launchctl print "gui/$(id -u)/cn.index-valuation.task-summary"
```

不带参数也只生成 plist。任务标识为 `cn.index-valuation.task-summary`，程序入口为本项目
`.venv/bin/python scripts/notify_task_summary.py send`，每天18:00执行；安装不立即发送消息，
没有循环重启。安装器只管理自己的任务，不替换五个业务任务或每分钟状态导出任务。

## 正文、保存与重试

`preview` 只读生成当前预览，不发送、不创建冻结摘要。`send` 使用当日北京时间，
先保存摘要及所依据的任务状态，再通过现有 Telegram 通道发送。
18:00前调用 `send` 会返回 `not_due` 并正常退出，不保存摘要、不发送，防止电脑次日早晨
唤醒时补触发昨天的任务，过早占用今天的通知。当天18:00后延迟执行时显示真实检查时间；
不根据今天的回执补造昨天的摘要。`retry` 只读取已冻结的指定日期摘要，不受18:00限制。
同一天已经保存的摘要保持原样，不因后续数据变化自动生成第二条通知。
ERP 排队成功不是表格更新成功；没有匹配的执行与回读结果时保持待确认。

| 路径 | 内容 |
| --- | --- |
| `data/scheduler/task-summaries/YYYY-MM-DD.json` | 生成时间、任务状态证据和冻结的汇总正文 |
| `data/scheduler/notifications/task-summary-YYYY-MM-DD.json` | 分段发送状态及已确认的 Telegram 消息 ID |
| `logs/task-summary.stdout.log` / `logs/task-summary.stderr.log` | 18:00任务的输出及错误日志 |

```bash
# 手动发送当天摘要；已确认发送的分段不会再次发送
uv run --locked python scripts/notify_task_summary.py send

# 只重试指定日期已冻结摘要中明确失败的分段
uv run --locked python scripts/notify_task_summary.py retry --date YYYY-MM-DD
```

送达未知（如请求超时、发送后进程中断）不会自动重发，先到 Telegram 核对，避免重复通知。
正文或收件目标冲突不会覆盖原发送记录。发送成功仅表示 Telegram API 已确认，不代表用户已读。
通知异常不改变五个业务任务的执行结果；停用 `TELEGRAM_ENABLED` 也不会停止这些业务任务。
原始回执与通知记录均在本机 `data/` 下，按项目备份策略保存。

## 停用

```bash
uv run --locked python scripts/install_task_summary_schedule.py --uninstall
```

此命令只卸载18:00汇总任务，保留历史摘要、通知回执及全部原有业务调度。
