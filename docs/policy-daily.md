# 每天16:00中国政策日报

Mac 宿主的独立 LaunchAgent `cn.index-valuation.policy-daily` 每天北京时间16:00运行，
包含周末与节假日。任务搜索中国市场相关的官方政策，完成后通过既有 Telegram Bot
向已绑定私人会话推送；18:00任务汇总也会报告本任务的执行与通知结果。

## 报告内容和研究边界

- 检查中央政府、人民银行、财政部、发展改革委、证监会、金融监管总局、住房城乡建设部、
  商务部八组官方来源；必要时补充行业和地方政府的正式发布。
- 今日栏目统计北京时间00:00—16:00；前日16:00后消息独立标注为补充。政策事实用官方
  公布时间，区分方向宣布、征求意见、文件发布和执行生效，不把旧政策转载当作新措施。
- 判断是否存在类似924的重大政策信号，说明规模、范围和传导机制的可比性；单项政策、
  常规逆回购续作或工作会议不自动构成全国政策转向。
- 每项列政策事实、受影响资产/行业、偏正面/偏负面/分化/中性/不确定、机制推断、置信度
  和官方链接。方向分析针对 A 股和港股，必要时说明人民币、债券及海外影响。
- 没有发现时只陈述在已检查来源中未发现，不声称绝对不存在；来源未完成检查、时间
  不明确或证据不足时为部分完成/失败，不将搜索失败伪装成“今日无政策”。

执行说明：[policy-daily-task.md](policy-daily-task.md)；结构化结果：
[policy-result.schema.json](policy-result.schema.json)；历史参照：
[924政策笔记](cn-policy-2024-0924.md)。程序检查来源域、部门覆盖、日期、字段及重大信号依据，
这些校验不等于自动证明模型引用的每句话都正确；报告提供原文便于复核。

当官网当天没有精确发布时间时，可保留当天日期并标为待核，报告降级为部分完成。
前日仅有日期的消息无法证明是在16:00后，不能作为已确认窗口事件。日报最多列八项重要事件。

## 安装、预览与运行

依赖现有本机 `.venv`、Codex CLI 登录和可用网络，无需新增 API key。复用 `.env` 的
`TELEGRAM_*` 绑定配置，凭据不进入提示词、plist或网页镜像。电脑需开机、用户已登录，
系统时区必须为 UTC+8；休眠会延后执行，不能保证离线时16:00准点送达。

```bash
# 只生成并检查，不运行搜索或推送
uv run --locked python scripts/install_policy_schedule.py --generate
plutil -lint data/scheduler/cn.index-valuation.policy-daily.plist
plutil -p data/scheduler/cn.index-valuation.policy-daily.plist

# 安装当前用户任务；安装本身不立即运行
uv run --locked python scripts/install_policy_schedule.py --install
launchctl print "gui/$(id -u)/cn.index-valuation.policy-daily"

# 仅检查当前查询窗口和执行资格
uv run --locked python scripts/run_policy_daily.py run --dry-run

# 实际只读搜索预览，保存独立预览，不发Telegram、不占用当天正式任务
uv run --locked python scripts/run_policy_daily.py preview

# 回看指定日期16:00；独立保存，真实执行时间不回拨，不占正式回执或自动推送
uv run --locked python scripts/run_policy_daily.py preview --date YYYY-MM-DD

# 手动触发正式流程；16:00前跳过，当天已有尝试不重复搜索/推送
uv run --locked python scripts/run_policy_daily.py run
```

程序使用新会话实时搜索和只读 shell 沙箱，不依赖历史会话队列。最长40分钟，超时或中断
会终止进程组并保存失败回执。检索不写数据库、表格或业务代码，消息由宿主在校验之后发送。
16:00前的唤醒补触发不会占用今日任务；晚于16:00启动仍以16:00为报告截止，不混入晚间消息。
断电期间无法生成的日报不会补造；次日照常研究当日窗口，不宣称覆盖整个停机期间。

## 回执、重试和网页

| 路径 | 内容 |
| --- | --- |
| `data/scheduler/policy-daily/runs/YYYY-MM-DD/state.json` | 唯一执行标识、开始/结束、研究状态和通知状态 |
| `data/scheduler/policy-daily/last-execution.json` | 任务页读取的最新执行回执，与当天状态核对 |
| `data/scheduler/policy-daily/runs/YYYY-MM-DD/report.json` | 经校验的研究结果、时间边界及冻结推送正文 |
| `data/scheduler/policy-daily/previews/` | 不参与正式调度与通知的试跑记录 |
| `data/scheduler/notifications/policy-daily-YYYY-MM-DD.json` | 分段发送状态和 Telegram 消息 ID |
| `logs/policy-daily.stdout.log` / `logs/policy-daily.stderr.log` | 调度输出与错误 |

同次执行目录也保存提示词、结构化原始结果和模型执行日志，供本机诊断；网页不读取模型原始日志。
退出0表示研究完成且通知确认（预览无通知），2表示研究部分完成或推送未确认；1为失败，
124为超时，130为中断。研究完成与消息送达分别记录，推送失败不会抹去有效研究结果。

只重试已冻结日报的通知：

```bash
uv run --locked python scripts/run_policy_daily.py retry --date YYYY-MM-DD
```

此命令不再次搜索；原内容或收件目标冲突时不覆盖发送记录。已确认发送的分段不重复发送，
送达未知（如发送后网络超时）先人工查看 Telegram，不自动重发。若研究失败，重试只发送
原失败结论；可用 `preview` 单独诊断，禁止删除正式回执伪装首次执行。

任务页显示本任务及原有五项业务任务。网页 Docker 对政策目录仅只读挂载；新增代码/挂载
后先创建目录，导出新的宿主调度快照，再 `make web-up`。正常日报更新不需重建网页。

```bash
mkdir -p data/scheduler/policy-daily
uv run --locked python scripts/export_schedule_status.py
make web-up

# 只卸载政策日报，保留历史与其他任务
uv run --locked python scripts/install_policy_schedule.py --uninstall
```
