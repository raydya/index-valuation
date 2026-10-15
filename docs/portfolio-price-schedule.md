# 持仓报价每日同步

从2026-10-16开始，每天北京时间09:00同步美股股票及美国上市ETF收盘价，17:00同步
A股、港股、场内ETF及开放式基金价格。包括周末和节假日，按实际已收盘交易日及已公布
基金净值取数，保留真实报价日期。目的地为原Portfolio文件的`Portforlio`工作表
（sheetId=1291175742）。表格读写仅使用Google Drive / Google Sheets连接器，不使用gws。

两份执行指令分别为[美股任务](portfolio-us-update-task.md)和
[中港及基金任务](portfolio-asia-update-task.md)。用户明确要求将原来2026-10-16的
两项单次任务改为每日任务；不改变其他指数估值或08:00风险溢价表任务。

## 配置与迁移

```bash
.venv/bin/python scripts/portfolio_schedule.py generate --start-date 2026-10-16
plutil -p data/scheduler/cn.portfolio.quotes.daily.us.plist
plutil -p data/scheduler/cn.portfolio.quotes.daily.asia.plist
.venv/bin/python scripts/portfolio_schedule.py run us --dry-run
.venv/bin/python scripts/portfolio_schedule.py run cn_hk_funds --dry-run
.venv/bin/python scripts/portfolio_schedule.py install --start-date 2026-10-16
launchctl print "gui/$(id -u)/cn.portfolio.quotes.daily.us"
launchctl print "gui/$(id -u)/cn.portfolio.quotes.daily.asia"
```

安装器先核对任务归属并备份，再加载两个每日任务，随后卸载并移走原来的
`cn.portfolio.quotes.once.20261016.us0900`与`cn.portfolio.quotes.once.20261016.ashk1700`。
发生安装错误时恢复此前配置和加载状态。备份位于`data/scheduler/backups/portfolio-<时间>/`。
旧`data/scheduled/portfolio-2026-10-16`目录仅作为历史配置保留，不再安装其单次任务。

每天的配置只有Hour、Minute，不限制月份、日期或星期，也没有LaunchOnlyOnce。
`--start-date`仅限制首次启用日，不是到期日。

## 运行与结果

沿用原单次任务的独立`codex --no-daemon exec --approve-for-me`执行方式、Google Drive
连接器和最长一小时的运行期限。不改变模型、沙箱或审批配置。当前用户需登录，系统时区
为UTC+8，电脑及网络可用，Codex和连接器授权有效；休眠或关机可能影响准点执行。

任务按北京时间日期分别保存状态，防止同一天重复提交，同时允许次日继续执行。失败或
超时也记录为当天已尝试，不自动再次写表；次日不受前一天失败影响。进程退出状态与表格
实际更新结果分别保存，不能把退出码0直接当作全部报价已更新。

- 调度日志：`logs/portfolio.us.stdout.log`、`logs/portfolio.us.stderr.log`，以及
  `logs/portfolio.cn_hk_funds.stdout.log`、`logs/portfolio.cn_hk_funds.stderr.log`。
- 每日执行产物：`data/scheduled/portfolio-daily/YYYY-MM-DD/<us|cn_hk_funds>.state.json`、
  `.result.md`、`.events.jsonl`、`.stderr.log`。检查结果文件中的报价日期、未更新项目和回读验证。
- `--dry-run`只检查指令、日期与文件，不创建运行记录，也不启动Codex或访问表格。

停用时分别对两个每日标签执行`launchctl bootout`；如需以后登录也不加载，将其对应plist
移出`~/Library/LaunchAgents`。运行结束不会自行删除每日配置。
