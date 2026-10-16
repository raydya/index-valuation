# 投资定时任务页面

入口为[本地估值网页的定时任务区](http://127.0.0.1:8000/#scheduled-tasks)，位于估值图之后。
顶栏“定时任务”可直接定位。页面展示 ERP、两个市场的指数更新和两个市场的持仓报价更新，
按实际安装配置列出计划时间、是否启用、下次计划及最近结果。所有时间按北京时间显示。

## 状态与证据

只读接口 `GET /api/scheduled-tasks` 每次汇总以下本机记录。网页约每分钟刷新，
也可手动“刷新状态”。切换指数或回看历史日期不改变此处的当前任务状态。

| 内容 | 来源与含义 |
| --- | --- |
| 调度配置 | 只读取5个已知投资任务的 LaunchAgent 配置与加载状态；无法读取时显示待核实 |
| 下次计划 | 按实际安装的每日时间与本机时区条件计算；是计划时间，不保证电脑休眠时准点执行 |
| 指数任务 | 新执行写入 `data/scheduler/index-runs/asia.latest.json` / `us.latest.json`；保存开始、结束、退出码和本次报告ID |
| 旧指数执行 | 从既有标准输出中的完整报告读取本次批次，用明确的运行ID关联数据库时间；证据不足时不冒充准确执行时间 |
| 指数数据覆盖 | 数据库当前方法的最新价格/PE及可用EPS日期，与最近一次定时执行分开，可包含手动补采或重建 |
| 持仓结果 | `data/scheduled/portfolio-daily/YYYY-MM-DD/` 下的状态和同名结果文件；退出0只表示已返回结果 |
| ERP提交 | `data/scheduler/erp-sheet/last-queued.json` 与 `last-attempt.json`；成功提交仅为排队 |
| ERP结果 | 同目录 `results/YYYY-MM-DD.json`；必须与排队记录的日期、会话、排队时间一致，并有真实回读验证 |

“已启用”表示任务加载状态，不代表数据已更新。“部分完成”保留已取得的数据和失败原因；
美国全市场按现有配置暂停采集时仍会明确显示，不作为调度未启动解释。
“已返回结果”不承诺全部项目成功，须展开阅读实际更新范围。PE口径未核实、核验源缺失及数据滞后保留原状态。

ERP任务会通过 `scripts/record_erp_result.py --input <结果JSON>` 写入结构化回执。
字段及完成条件见[每日执行指令](erp-sheet-update-task.md)。初次接入页面时，已将2026-10-16的ERP执行结果
根据原会话完成记录和连接器回读证据补录；实际执行时间保留，不把补录时间当成完成时间。
后续任务若只排队、未产生匹配结果，页面继续显示待确认。

## 读取边界与故障

页面只读取已知任务、限定大小的项目结果文件和数据库摘要，不请求行情供应商或Google表格，
不读取Keychain凭据或其他Codex会话。结果正文作为纯文本显示，数据缺口不会被渲染成成功。
数据库、某份结果文件或调度查询不可用时，其余任务继续展示；刷新失败时明确标记保留的旧记录。

新指数执行先写“执行中”回执，完成或超时后更新终态。标准错误仍实时记录，标准输出在运行结束后
写回原有launchd日志；退出码语义不变，不改变每日时间、市场范围、采集来源或表格写入授权。
正常状态刷新不需重启，后端代码或配置改变后需重启网页服务。

## 验证

运行 `make check`、`make test`；数据库查询变更运行 `make test-db`。
网页交互使用项目的Playwright WebKit，检查5项任务、展开结果、刷新与错误恢复、手机布局，
并运行 `scripts/check_web.py` 核对原估值图、历史回看与CSV仍可用。浏览页面不会触发实际任务。

```bash
PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/playwright" uv run --locked python scripts/check_scheduled_tasks_web.py
PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/playwright" uv run --locked python scripts/check_web.py
```
