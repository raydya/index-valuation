# 执行本次指数风险溢价表每日更新

这是用户已授权、每天北京时间08:00触发的表格同步任务。现在执行本次更新，不要创建或调整定时任务，不要修改项目代码或其他工作表。

用户明确要求使用 Google Drive 连接器；所有表格读取与写入只能通过本会话的 Google Drive / Google Sheets 连接器。不得使用 Google CLI（gws）或其他 Google CLI，也不要改用 Google Chrome。连接器不可用时报告未更新，不更换成用户禁止的访问方式。

使用 Google Drive / Google Sheets 连接器和 google-sheets 技能，在原文件中更新指数风险溢价表：
https://docs.google.com/spreadsheets/d/17B0OcBwSrwkBrcM8BlEDNfbtNeL2ZsXpGX_HDr5b4kQ/edit?gid=1665776241#gid=1665776241

目标工作表为 Index ERP，sheetId=1665776241。用户已授权每天同步更新指数盈利收益率和国债收益率，无需每次重复确认。先读取工作表元数据、A1:P10 的值、公式和备注，核对名称与范围；写入前重新读取，避免覆盖并发修改。若表格结构变化，以实际表头和指数身份定位，不盲目写固定行号。

当前结构：A列指数，B列PE，C列盈利收益率（=1/B），D列挂钩国家，E列10年国债收益率（引用O:P），F列利差，G列手动调整，H列风险惩罚。指数行目前为沪深300第2行、A500第3行、科创50第4行、创业板第5行、恒生科技第6行、SPY第7行、QQQ第8行、红利低波第10行。第9行是用户手动调整，保留。P2是美国国债，P3是中国国债。

获取执行时最新已公布、日期明确的数据。只更新必要的B列PE及P列国债输入，并维护实际数据日期、来源网址、口径、复核时间和限制备注；保留收益率、利差、风险惩罚公式、国家映射、手动调整、阈值、格式和其他工作表。盈利收益率是PE倒数，不是股息率或历史年化投资回报。优先使用API、直接网页/PDF和网络搜索，不使用Google Chrome。

数据源与口径：

1. 沪深300（000300）、中证A500（000510）、科创50（000688）、中证红利低波动（H30269）：使用中证官方日度滚动PE，接口 https://www.csindex.com.cn/csindex-home/perf/index-perf ，参数indexCode、startDate、endDate，日期格式YYYYMMDD，字段peg。核对indexCode、名称和tradeDate，选择最近已收盘且已公布的一日。此处peg为该接口的官方滚动市盈率字段，不是市盈增长比率。可查询近期窗口并将endDate设为区间末日次日，再过滤尚未收盘日期。2026-10-15已从月末事实表升级为官方日度值，日常更新不得回退到更旧的月末值。官方计算用股本口径与第三方可能不同，来源相同的网页/PDF不能算独立交叉核验。
2. 创业板指（399006）：https://www.cnindex.com.cn/index/indexList?channelCode=-1&rows=2000&pageNum=1 的peDynamic（官网PE滚动），不用peStatic。通过 https://www.cnindex.com.cn/index/queryDay 核对日期；毫秒时间戳转Asia/Shanghai。
3. SPY：https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-500-etf-trust-spy 的Index Characteristics下Price/Earnings及该栏目自己的日期，沿用标普500指数benchmark proxy；不用Price/Earnings Ratio FY1前瞻值，不以页面其他栏目日期代替估值日期。
4. QQQ：https://www.portfoliovisualizer.com/fund-performance?benchmark=PSLDX&s=y&symbol=QQQ 的Price/Earnings(TTM)，并读取Fund fundamentals data as of日期。沿用原表Morningstar经Portfolio Visualizer展示的ETF持仓加权TTM口径，明确不是纳斯达克100整体指数PE；数据集日期不等于已经核实的持仓或收盘估值日期。若网页搜索缓存滞后，以直接取得的完整原文为准并说明。
5. 恒生科技：原B6的PE数据日期及TTM口径未核实。官方参考文件 https://www.hsi.com.hk/content/dam/wpb/hsil/index_operation/historical_pe/en_hk/hstech/hstech.xlsx 为月末加权PE，不能在未确认盈利期时当作TTM替换。继续寻找日期与口径均明确的同类数据；仍不能核实时保留原值和未核验备注，并在结果中明确该收益率不是最新已核验数据，不默认为已成功更新。
6. 美国国债：美国财政部Daily Treasury Par Yield Curve Rates，https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve&field_tdr_date_value_month=YYYYMM ，月份使用执行时实际月份，必要时回查上一月。采用最新公布行的10 Yr名义平价/CMT收益率，不使用实际利率、短期票据或其他期限。百分数除以100后写P2。
7. 中国国债：https://yield.chinabond.com.cn/cbweb-cbrc-web/cbrc/more ，核对表头日期及ChinaBond Government Bond Yield Curve的10年标准期限日终到期收益率，不使用商业银行或信用债曲线。百分数除以100后写P3，保留源精度。

数据尚未发布、周末无新值、权限拒绝或来源失败时，保留已有输入和真实数据日期，注明原因，不伪造今天的数据，不以价格涨跌推算PE，不把旧值标成新值。对来源的盈利期、加权和亏损处理不作超出证据的统一口径承诺。原表不同市场或指标日期可以不同，但必须透明标注。

使用精确十进制核对PE倒数和利差，批量写入后重新读取，验证C=1/B、E正确引用对应国债、F=C-E，无公式错误；确认未改变其他输入、公式和格式。向用户报告本次实际修改的项目、各项数据日期、未变化项及未核验/失败项，并提供原表链接。只在成功回读后报告更新完成；若连接器不可用或写入失败，明确报告未更新。

执行结束后，按本次 `--output-schema` 指定的结构返回一个 JSON 对象，作为最终回答。宿主运行器会记录真实开始/结束时间及唯一执行标识，验证后自动写入完成回执；不要自行修改调度、代码、回执或使用旧排队标识。最终对象包括：

- `status`：全部目标已核验为 `completed`；存在未更新或未核验项为 `partial`；无法读取、写入或回读验证为 `failed`。不能仅凭排队成功填 completed。
- `readback_verified`：布尔值，只有真实回读验证完成才为 true；completed 和 partial 必须为 true。
- `summary`：一句结果摘要；`issues`：未更新、未核验或失败原因的字符串列表；`result_text`：本次最终报告正文。
- `data_items`：各目标的列表，每项包含 `name`、`date`（真实数据日期，未知为 null）、`complete_date`（已核验日期，未知为 null）、`status`（available / partial / unknown）、`note`（口径及限制）。QQQ 的数据集日期不得写成已核实的收盘估值日；恒生科技未知日期保持 null。

`data_items` 必须涵盖本次全部8个指数/ETF输入及中美两个国债输入，标准名称分别为：沪深300、中证A500、科创50、创业板指、恒生科技、SPY、QQQ、红利低波、美国10年国债、中国10年国债。每项只列一次，未更新项也需列出原因；不能只列成功项来报告 completed。`result_text` 保存完整简短报告及原表链接，字段值保留实际日期和来源口径。最终回答只输出 JSON，不用代码围栏。不要在结果中保存凭据、完整工具日志、本机路径或与任务无关的会话内容；不要另外发送 Telegram 或消息，定时汇总会读取宿主保存的回执。
