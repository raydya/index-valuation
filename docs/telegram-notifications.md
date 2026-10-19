# Telegram 每日估值通知

每日中港任务在北京时间17:00启动，美股任务在09:00启动；任务结束后通过 Telegram Bot 向一个已绑定的私人会话推送。无需网页服务运行，也不需要新增定时任务。机器、数据库和行情接口仍须满足原有运行条件。

## 内容与执行规则

- 通知包含本轮执行状态、执行时间，以及各指数的实际数据日期、PE、历史分位、恐慌/普通/贪婪估值区间和来源缺口。估值区间复用网页的同口径五年 P10/P90 规则；样本不足不判断，不生成买卖指令。
- 日期与本轮独立市场日历的最后交易日比较。周末不虚构新数据；日期落后、日历缺失、采集暂停和本轮未执行均明确说明。分红再投资数据的失败或缺口单独提示。
- 来源告警覆盖请求失败、价格缺口及主源估值缺口；明确不支持的来源不报成请求故障，纯价格参考源未提供 PE 不报成主源估值缺失。原始逐源覆盖仍保存在完整采集报告中。
- 每个指数完成后立即落盘本批次 JSONL；中途失败或一小时超时仍能报告之前已完成的项目。通知使用本轮固化报告，不用数据库当前最新值猜测本轮是否成功。
- 每批次合并发送，超过 Telegram 长度上限时分段。消息为纯文本，正文不会发送原始日志、异常、凭据或本机文件路径。
- 通知失败不改变原采集结果或退出码。每段的 Telegram `message_id` 保存后不重复发送；发送超时、响应不明或发送中进程中断标为 `delivery_unknown`，不自动重发，以免重复。`sent` 表示 Telegram API 已确认发送，不代表用户已阅读。

## 配置和绑定

1. 将 BotFather 提供的 token 写入本机、不提交的 `.env`：

   ```dotenv
   TELEGRAM_BOT_TOKEN=你的BotToken
   ```

2. 选择一个只在本次绑定使用的8—40位字母数字绑定码；在 Telegram **私聊**该 Bot，发送 `/start index_valuation_<绑定码>`，然后执行：

   ```bash
   uv run --locked python scripts/telegram_notify.py bind --code <绑定码>
   ```

   脚本只接受唯一匹配的私人会话，核对 `getChat` 后把 `TELEGRAM_CHAT_ID` 和 `TELEGRAM_ENABLED=true` 写入 `.env`。普通 `/start` 不会自动绑定，避免已有 Bot 的其他订阅者被误选。绑定读取不传 `offset`，不确认其他应用的更新；已有 webhook 时直接停止，不删除 webhook。Bot 若已由其他程序接收更新，可自行将本人的数字用户 ID 写入 `.env` 的 `TELEGRAM_CHAT_ID`（私人会话 ID 与用户 ID 相同），再运行 `uv run --locked python scripts/telegram_notify.py enable`；该命令核对私人会话后才启用。

3. 检查并实际试发：

   ```bash
   uv run --locked python scripts/telegram_notify.py status
   uv run --locked python scripts/telegram_notify.py test
   ```

   `status` 只显示配置是否存在。`test` 明确标为通道测试，不表示已运行新的行情更新。未启用时不发送。停止通知只需将 `TELEGRAM_ENABLED=false`，估值更新继续运行。

如果本机访问 Telegram 需要代理，在 `.env` 中配置供定时任务使用的 `TELEGRAM_PROXY_URL=http://127.0.0.1:<端口>`。支持 HTTP(S) 代理；代理程序必须在任务运行时可用。无需把 token、代理密码或私人会话 ID 放进 plist，也不要放进命令行或提交到 Git。

## 查看、补发与排查

新版本调度保存：

| 路径 | 用途 |
| --- | --- |
| `data/scheduler/index-runs/{asia,us}.latest.json` | 本轮批次标识、终态、已完成 run IDs 和独立通知状态 |
| `data/scheduler/index-runs/<batch-id>.results.jsonl` | 本批次逐指数固化报告 |
| `data/scheduler/notifications/<batch-id>.json` | 已发送正文、每段确认 ID、发送状态；不保存 token/原始 chat ID |

```bash
# 只预览新版本调度已经完成的最近批次，不发送
uv run --locked python scripts/telegram_notify.py preview --market asia

# 发送该批次（已有确认回执时不会重复）；美股将 asia 改成 us
uv run --locked python scripts/telegram_notify.py send-latest --market asia

# 只补发已知失败的剩余分段，不重新采集数据
uv run --locked python scripts/telegram_notify.py retry --batch-id <批次UUID>
```

以上预览和补发命令需要新版本调度生成的批次回执；旧版回执没有批次 journal，不能据此假称本轮完整结果。`delivery_unknown` 需先在 Telegram 中人工核对，不提供自动强制重发。`conflict` 表示同一批次正文或目标配置发生变化，程序拒绝覆盖原发送记录。配置缺失或失效、用户屏蔽 Bot、网络失败均有明确本地状态；不通过通知异常中断采集。

官方依据：[Telegram Bot API](https://core.telegram.org/bots/api)、[Bot 使用限制](https://core.telegram.org/bots/faq)。发送使用 `sendMessage`；用户须先与 Bot 建立私聊。实现没有额外 AI 服务或新的第三方推送平台。

## 本机验证（2026-10-19）

已用专用绑定消息核对私人会话，并启用现有 Bot。19:57—19:58 实际执行一次美股增量：标普500和纳斯达克100均覆盖2026-10-16，美国全市场保持暂停，因此批次为 partial。任务结束自动发送成功，Telegram 返回 message ID 791；随后更正同一条通知中的非适用来源提示，保留修订哈希。再次执行 `send-latest` 返回同一确认记录，没有另发消息。两项 LaunchAgent 均已加载并指向当前脚本，继续使用09:00和17:00的原有时间。

683项非数据库测试、Ruff、格式及差异检查通过。测试覆盖批次中途退出、暂停、残缺 journal、陈旧数据、缺少交易日历、口径不一致、分段去重、送达未知、私人绑定、已有 webhook、网络错误及日志 token 脱敏。本次未修改数据库结构，也未更改价格/PE数据源。凭据只保存在 Git 忽略的本机 `.env`。
