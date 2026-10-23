# Telegram 每日估值通知

每日中港任务在北京时间17:00启动，美股任务在09:00启动；任务结束后通过 Telegram Bot 向一个已绑定的私人会话推送。无需网页服务运行，也不需要新增定时任务。机器、数据库和行情接口仍须满足原有运行条件。

每天18:00另有独立的[六任务汇总](task-summary-notifications.md)，合并报告两组指数、两组持仓、ERP 表格及中国政策日报的执行结论。它复用同一私人会话，原批次的逐指数估值通知继续发送。

## 内容与执行规则

- 正文通过 Telegram 原生富消息发送，标题为“指数估值：{实际日期}”，下方表格每个指数一行，只保留四列：指数、PE、历史分位、估值状态。表格增加表头、边框和隔行底色，使用紧凑间距，PE及历史分位数字右对齐；不添加执行时间、解释、来源告警或缺口详情。
- 日期使用本批次固化报告中该指数的实际数据交易日，不使用任务执行或消息发送日期。同批次日期不一致时，按实际日期分别生成标题和表格；日期未知的指数归入独立的“指数估值：暂无”组，不归到其他日期。周末和日期落后的数据仍保留真实日期；PE、历史分位缺失时显示“暂无”，不以旧批次或其他日期的数据补值。
- 估值状态沿用网页的同口径五年 P10/P90 规则，正常可判断时为“恐慌”“普通”“贪婪”。原有方法、来源、日期与数值一致性检查及样本要求继续生效；样本不足、采集暂停、本轮无有效报告或信号不可用时显示“暂无判断”。历史分位已有有效数值时保留数值，短历史不追加括号说明；简化正文不改变其计算口径，不生成买卖指令。
- 仅在有效估值状态为“贪婪”或“恐慌”时，分别在非空历史分位及状态前加相同的🔴或🟢标记。普通、暂无及暂无判断不加彩色标记；颜色跟随已验证的估值状态，不根据百分比另外生成判断。原生表格没有自定义红绿字体字段，因此使用彩色标记，不声称文字本身被设为红色或绿色。
- 例如标题为 `指数估值：2026-10-22`，一行四个单元格为 `沪深300`、`13.25`、`35.2%`、`普通`（仅为格式示例）。缺少本轮报告、批次报告不可读或采集暂停时也保留四列，缺失数值填“暂无”，状态填“暂无判断”，不添加异常说明前缀。本轮没有匹配市场的指数时不发送空摘要。
- 本轮执行状态、交易日历、新鲜度、来源失败和价格/PE/全收益缺口继续保存在本地批次回执、逐指数报告和日志中，通知正文不附加这些详情。18:00六任务汇总与16:00政策日报的内容和发送流程不受此格式调整影响。
- 每个指数完成后立即落盘本批次 JSONL；中途失败或一小时超时仍能报告之前已完成的项目。通知使用本轮固化报告，不用数据库当前最新值猜测本轮是否成功。
- 当前每批次的少量指数按日期分组，所有标题和四列表格合为一条富消息，通过 `sendRichMessage` 发送，不按普通文本消息的4096字符上限拆表。纯文本仅用于本地预览和兼容旧通知回执，正文不会发送原始日志、异常、凭据或本机文件路径。
- 通知失败不改变原采集结果或退出码。Telegram `message_id` 保存后不重复发送；发送超时、响应不明或发送中进程中断标为 `delivery_unknown`，不自动重发，也不自动改用纯文本再次发送，以免重复。`sent` 表示 Telegram API 已确认发送，不代表用户已阅读或已验证客户端显示。

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
| `data/scheduler/notifications/<batch-id>.json` | 冻结的 `rich_message` 表格、纯文本预览、确认 ID及发送状态；不保存 token/原始 chat ID |

```bash
# 以纯文本预览已经完成的最近批次，不发送
uv run --locked python scripts/telegram_notify.py preview --market asia

# 发送该批次（已有确认回执时不会重复）；美股将 asia 改成 us
uv run --locked python scripts/telegram_notify.py send-latest --market asia

# 只重试已知失败的冻结通知，不重新采集数据
uv run --locked python scripts/telegram_notify.py retry --batch-id <批次UUID>
```

预览和 `send-latest` 需要带批次 journal 的调度回执，不能把没有 journal 的旧回执当成本轮完整结果。`retry` 优先使用通知回执中已冻结的 `rich_message` 原样重试；旧通知回执没有该字段时，保持原有纯文本及分段，不转换为新表格。`delivery_unknown` 需先在 Telegram 中人工核对，不提供自动强制重发。`conflict` 表示同一批次正文、表格或目标配置发生变化，程序拒绝覆盖原发送记录。配置缺失或失效、用户屏蔽 Bot、网络失败均有明确本地状态；不通过通知异常中断采集。

原生表格采用 [`sendRichMessage`](https://core.telegram.org/bots/api#sendrichmessage) 和 [`InputRichBlockTable`](https://core.telegram.org/bots/api#inputrichblocktable)。官方[富消息限制](https://core.telegram.org/bots/api#rich-message-limits)为文本最多32768 UTF-8字符、500块（表格行也计入）、16层嵌套、50个媒体和每表20列；当前四列表格远低于这些限制。API成功返回普通 `Message`，继续记录其 `message_id`。用户须先与 Bot 建立私聊；客户端显示需支持富消息，官方未提供最低版本或旧客户端的显示保证。实现没有额外 AI 服务或新的第三方推送平台。

## 本机验证（2026-10-19）

已用专用绑定消息核对私人会话，并启用现有 Bot。19:57—19:58 实际执行一次美股增量：标普500和纳斯达克100均覆盖2026-10-16，美国全市场保持暂停，因此批次为 partial。任务结束自动发送成功，Telegram 返回 message ID 791；随后更正同一条通知中的非适用来源提示，保留修订哈希。再次执行 `send-latest` 返回同一确认记录，没有另发消息。两项 LaunchAgent 均已加载并指向当前脚本，继续使用09:00和17:00的原有时间。

683项非数据库测试、Ruff、格式及差异检查通过。测试覆盖批次中途退出、暂停、残缺 journal、陈旧数据、缺少交易日历、口径不一致、分段去重、送达未知、私人绑定、已有 webhook、网络错误及日志 token 脱敏。本次未修改数据库结构，也未更改价格/PE数据源。凭据只保存在 Git 忽略的本机 `.env`。
