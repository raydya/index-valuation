# 每天18:00的六任务汇总通知

北京时间每天18:00，独立的 Mac LaunchAgent 汇总以下六项任务，通过已经绑定的
Telegram Bot 私聊推送：ERP 表格、美股持仓、中港及基金持仓、美股指数估值、中港指数估值、
中国政策日报。政策日报每天16:00检索并独立推送，18:00仅汇总其执行时间与执行结果。
包括周末及节假日；排队、执行中、失败、部分完成和缺少回执会分别说明。

此汇总读取已有执行证据，不重新采集行情、更新表格或搜索政策。08:00 ERP、09:00/17:00业务任务、
16:00政策日报以及指数批次结束后的 Telegram 通知继续按原计划运行。
网页容器无需运行，汇总由 Mac 执行；更新汇总代码后，宿主下次定时执行即使用新代码，无需重建 Docker 镜像。
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
没有循环重启。安装器只管理自己的任务，不替换六个业务任务或每分钟状态导出任务。

## 正文、保存与重试

Telegram 正文以当日日期为标题，使用原生三列表格：**任务名称、执行时间、执行结果**，
六项任务各一行。执行时间来自匹配当天任务的真实回执；未见当天回执或时间异常时明确标明待确认。
正文只展示这三列，不附计划时间、来源与数据缺口、政策内容、持仓结果摘录、推送状态、警告或尾注。
这些详细证据仍保存在本地冻结摘要的 `snapshot` 和 `index_batches` 中，便于排查。

结果采用结构化状态：`completed` 显示“完成”、`partial` 显示“部分完成”、`failed` 显示“失败”、
`running` 显示“执行中”、`queued` 显示“排队中”。持仓任务的 `finished` 只表示执行结束，
显示“已结束，结果待确认”；不从自由文本中的“成功”或“完成”等字样推断业务成功。
ERP 排队成功不是表格更新成功；没有匹配的执行与回读结果时保持待确认。

`preview` 只读生成当前预览，不发送、不创建冻结摘要。`send` 使用当日北京时间，
先保存摘要及所依据的任务状态，再通过现有 Telegram 通道发送。新摘要同时保存纯文本预览 `text`
和原生表格 `rich_message`；纯文本预览与表格使用相同的三列内容。
18:00前调用 `send` 会返回 `not_due` 并正常退出，不保存摘要、不发送，防止电脑次日早晨
唤醒时补触发昨天的任务，过早占用今天的通知。当天18:00后延迟执行时，真实检查时间保存在本地摘要中；
不根据今天的回执补造昨天的摘要。`retry` 只读取已冻结的指定日期摘要，不受18:00限制。
同一天已经保存的摘要保持原样，不因后续数据变化自动生成第二条通知；旧冻结摘要的重试继续使用
原有文本和格式，不转换为新表格。
政策日报使用 `data/scheduler/policy-daily/last-execution.json`，并核对同日 `runs/YYYY-MM-DD/state.json`
的执行身份、时间和状态；不读取原始模型日志，不把旧日政策结论当作今日结论。完成表示研究回执
有效，不表示进行了表格回读，也不表示穷尽所有政策；来源覆盖限制和推送状态保留在本地证据中。

| 路径 | 内容 |
| --- | --- |
| `data/scheduler/task-summaries/YYYY-MM-DD.json` | 检查时间、`snapshot` / `index_batches` 证据、冻结的 `text` 与新摘要的 `rich_message` |
| `data/scheduler/notifications/task-summary-YYYY-MM-DD.json` | 分段发送状态及已确认的 Telegram 消息 ID |
| `logs/task-summary.stdout.log` / `logs/task-summary.stderr.log` | 18:00任务的输出及错误日志 |

```bash
# 手动发送当天摘要；已确认发送的消息不会再次发送
uv run --locked python scripts/notify_task_summary.py send

# 只重试指定日期已冻结摘要中明确失败的发送
uv run --locked python scripts/notify_task_summary.py retry --date YYYY-MM-DD
```

送达未知（如请求超时、发送后进程中断）不会自动重发，先到 Telegram 核对，避免重复通知。
正文或收件目标冲突不会覆盖原发送记录。发送成功仅表示 Telegram API 已确认，不代表用户已读。
通知异常不改变六个业务任务的执行结果；停用 `TELEGRAM_ENABLED` 也不会停止这些业务任务。
原始回执与通知记录均在本机 `data/` 下，按项目备份策略保存。

## 停用

```bash
uv run --locked python scripts/install_task_summary_schedule.py --uninstall
```

此命令只卸载18:00汇总任务，保留历史摘要、通知回执及全部原有业务调度。
