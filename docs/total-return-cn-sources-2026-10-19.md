# A 股指数分红再投资序列来源核验（2026-10-19）

本次核验用于给现有价格年化增加独立的“分红再投资年化”。只核验和试取全收益指数收盘，不改变价格指数、PE、隐含 EPS 和估值带的口径。不用 ETF 复权价格或当前股息率补造全收益历史。

## 官方身份

| 项目 key | 价格指数 | 对应全收益代码 | 官方基日 / 建议采集下限 | 身份依据 |
| --- | --- | --- | --- | --- |
| `csi300` | 沪深300 / 000300 | **H00300** | 2004-12-31 | [中证官方单张](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000300factsheet.pdf)、[编制方案 §4.6](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000300_Index_Methodology_cn.pdf) |
| `csia500` | 中证A500 / 000510 | **000510CNY010** | 2004-12-31 | [中证官方单张](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000510factsheet.pdf) |
| `sse` | 上证指数 / 000001 | **000888** | **2020-07-21**，基点3320.89 | [上交所编制方案](https://www.sse.com.cn/market/sseindex/diclosure/c/10759849/files/3ce41a1428a3490ab31d78d5543e596c.pdf)、[2024年代码调整公告](https://www.sse.com.cn/market/sseindex/diclosure/c/c_20240715_10759849.shtml) |
| `chinext` | 创业板指 / 399006 | **399606** | 2010-05-31 | [国证官方编制方案](https://www.cnindex.com.cn/docs/gz_399606.pdf)、[指数详情](https://www.cnindex.com.cn/module/index-detail.html?act_menu=1&indexCode=399606) |
| `star50` | 科创50 / 000688 | **000688CNY01** | 2019-12-31 | [中证官方单张](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000688factsheet.pdf)、[上交所指数列表](https://www.sse.com.cn/market/sseindex/indexlist/index.shtml?classid=010202) |
| `csidividend` | 中证红利 / 000922 | **H00922** | 2004-12-31 | [中证官方单张](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000922factsheet.pdf) |
| `divlowvol` | 红利低波 / H30269 | **H20269** | 2005-12-30 | [中证官方单张](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/H30269factsheet.pdf) |

中证同时区分全收益和净收益：全收益计入税前现金红利再投资，净收益计入税后现金红利再投资，不能混用。[中证股票指数计算规则 §2.2](https://oss-ch.csindex.com.cn/contract/cms_add/20240726155028-%E3%80%8A%E4%B8%AD%E8%AF%81%E6%8C%87%E6%95%B0%E6%9C%89%E9%99%90%E5%85%AC%E5%8F%B8%E8%82%A1%E7%A5%A8%E6%8C%87%E6%95%B0%E8%AE%A1%E7%AE%97%E8%A7%84%E5%88%99%E3%80%8B.pdf)

创业板R官方方案规定全收益指数在派息除息日将样本上一收市价更新为交易所除权参考价；价格指数对现金分红不作这一调整。这里选取399606全收益版本，不以其他净收益或ETF版本替换。[国证编制方案 §9](https://www.cnindex.com.cn/docs/gz_399606.pdf)

## 可用接口与实测

### 中证官网公开 Excel 下载

原有 `GET /csindex-home/perf/index-perf` 在本次会话中先成功、随后出现HTTP403。本研究没有继续重试该端点，没有更换身份或绕过验证码、登录、付费限制。

正常公开指数详情页的“下载”按钮调用独立的 Excel 导出功能；参数从官网公开页面脚本读取，不执行脚本：

```text
POST https://www.csindex.com.cn/csindex-home/exportExcel/downloadindex-perf?language=CH
Content-Type: application/json

[{"indexCode":"H00922","startDate":"20261012","endDate":"20261017"}]
```

返回HTTP200，`Content-Type: application/vnd.ms-excel;charset=utf-8`，实际文件为ZIP封装的 **XLSX**。本次响应使用`inlineStr`，单一sheet包含日期、指数代码、中英文全称、收盘等列；多指数请求也合并在同一sheet，因此必须逐行核验代码，不能凭工作簿名或行数认定身份。

2026-10-12至2026-10-16，每个代码均取得5个收盘记录；工作簿中对应英文名称明确含`Total Return Index`：

| 全收益代码 | 2026-10-16官方收盘 | 早期短窗口实际返回 |
| --- | ---: | --- |
| H00300 | 6270.45 | 2004-12-31基点1000；2005年1月有真实变动，2002年试窗为空 |
| 000510CNY010 | 7404.60 | 2004-12-31基点1000，2005年1月有记录 |
| 000888 | 4335.81 | 2020-07-21基点3320.89，其后有记录 |
| 000688CNY01 | 1403.75 | 2019-12-31基点1000，2020年1月有记录 |
| H00922 | 12137.96 | 2004-12-31基点1000，2005年1月有记录 |
| H20269 | 24615.16 | 2005-12-30基点1000，2006年1月有记录 |

**边界现象：** 这些导出会额外包含基日前一个交易日的同值基点行（例如H00922的2004-12-30=1000）；正式采集必须以已确认基日作为独立下限，过滤这类行。沪深300原价格历史起点为2002年，但不能把这个起点直接套给全收益序列。上证收益也不能回填到上证价格指数1990年起点。

以上是短窗口核验，不表示已经验证全历史无缺口。生产回填应分年取数、报告真实缺口，不补值或移日期。

### 国证与富途 OpenD

国证官方接口可直接读取创业板R：

```text
GET https://hq.cnindex.com.cn/market/market/getIndexDailyDataWithDataFormat
    ?indexCode=399606&startDate=2026-10-12&endDate=2026-10-17&frequency=day
```

JSON明确返回`indexCode=399606`、`indexName=创业板R`、`indexEName=ChiNext TRI`；最近5个交易日都有记录。2010-05-31至2010-06-10短窗返回9条，首日1000。该接口也包含查询结束日，需由调用者过滤范围。

富途SH/SZ指数目录分别有321/400个记录；目标中可验证的映射为`SH.000888`和`SZ.399606`。两者日K读取均成功。399606近期5天与国证逐日完全一致；000888与中证导出的差异仅为小数位舍入，例如2026-10-16富途4335.8064、中证4335.81。渠道可能共享交易所或指数机构上游，这不是两套独立编制方法的验证。

其余5个目标全收益代码未在本次富途SH/SZ指数目录中匹配到。国证接口对H00300、H00922、H20269、000510CNY010、000688CNY01、000888均返回空数据和空名称，不能视为可用交叉来源。东方财富7个试取均发生`RemoteProtocolError`连接中断，属于本次网络/来源失败，不能据此认定代码不存在。

## 实现约定与证据

- 独立适配器：[csindex_returns.py](../src/index_valuation/providers/csindex_returns.py)。不改原来的CSI价格/PE适配器。按年调用官网公开Excel导出；只产出`gross_total_return`价格观测，不产出PE或EPS。原始二进制及请求元数据分别按哈希归档。
- 对证券代码、全收益名称、日期、有限正价格和已收盘时间进行校验；发现净收益、错代码、重复日期或结构错误就拒绝。HTTP401/403/429停止后续请求；其他网络失败连续3段后停止并报告未请求区间。
- 一次性研究响应位于`data/source-research/total-return-cn-2026-10-19/`（忽略提交），包括`csindex_public_export_recent.xlsx`、`csindex_public_export_early.xlsx`及其请求和解析JSON，`cnindex_399606_*.json`、`futu_catalog_*.json`、`futu_history_*.json`，以及官方页面/方法文件。
- 年化计算应用所选区间内全收益指数实际端点和实际间隔天数；价格年化与分红再投资年化须使用可比较的日期。没有端点不能套用当前股息率，也不能把部分历史的年化标成整个所选区间。

## 回填时出现的限制与边界记录

2026-10-19首次生产回填中，沪深300、中证A500、上证收益均取得覆盖全部应有交易日的官方数据；随后科创50、中证红利、红利低波的首个年度请求返回HTTP403。响应正文为网站应用防火墙阻断页，没有`Retry-After`，不能据此断定存在某个已知数值的官方限速规则。

停止请求约3分钟后，11:51:14仅使用完全相同身份和参数的H00922短窗口正常请求，恢复HTTP200。11:51:59按官网支持的多指数参数试取剩余三个全收益序列的多年范围，仍返回HTTP403，随即停止；没有更改headers、cookie或身份。官网界面代码包含5年、10年和自定义区间，但本次多年下载未获验证，因此实现仍保持已验证的年度分块。读适配器增加**进程内共享、请求起点至少间隔2秒**的保守节流；这只是减少突发访问，不保证网站接受，也不自动重试403。

前三个CSI回填的日历隔离记录可解释为：沪深30023条（22个元旦日期，加2018-06-18），A50022条（均为元旦），上证收益6条（均为元旦）。原始Excel通常会额外生成请求起始日的锚点行，并复制窗口内首个交易日点位，例如H00300在2005-01-01和2005-01-04均为982.79，上证收益2021-01-01和2021-01-04均为3514.25。系统没有生成这些值，交易日历过滤已阻止它们入库。沪深3002018-06-18那条是不同的原始来源异常，值4614.35并不等于前后交易日，不能一概称为复制；继续保留原响应并隔离，不移到别的日期。

恢复试取和多年拒绝的证据保存在研究目录的`csindex_export_cooldown_probe.*`与`csindex_export_remaining_full.*`。原先已合法取得的三个缺失指数最近5日及基日前后短窗口仍有原始证据，但不能据此宣称已经补齐完整历史。

之后一次正常低频回填取得科创50完整日度数据；中证红利再次遭403后，整个批次停止，没有继续尝试红利低波。对红利低波改为离线恢复此前已经取得的合法公开响应：仅导入H20269的2026-10-12至2026-10-16五个真实收盘，不导入与近期相隔约20年的基日短样本。原始采集时间保留为2026-10-19T03:40:57.696484Z，恢复过程没有发出外网请求。

批量响应解析只有在调用者明确传入原始请求中的`requested_codes`时，才允许跳过同一工作簿中其他已请求指数；未知代码仍拒绝，普通单指数读取的严格行为不变。原始工作簿和原请求元数据分别按哈希归档，恢复记录指向`data/raw/csindex_total_return/53033d87c99bed7e9c33e0a01d1e17d8d45f7595369e5354e39fa40c3bfad90a.json`。本次恢复报告为`data/exports/divlowvol-total-return-cached-recovery-0f8d0446-0374-42e5-a7f3-1ece2beed419.json`，状态`partial`，历史仍缺5043个交易日。重建的网页和CSV可显示这五个真实点位，但四天折算的年化不能描述成所选五年或完整历史年化。

## OpenD专项复核

2026-10-19在本机当前已登录的富途OpenD中，**未找到中证红利全收益H00922或中证红利低波动全收益H20269的可用映射**。这一结论只描述本次目录和实际日线响应，不推断富途永久不支持，也不因搜索结果缺失就认定证券不存在。

本次读取SH、SZ、HK、US四个市场的指数目录，分别返回321、400、203、537条。按目标代码及`Dividend`、`红利`、`Total Return`、`全收益`等名称核对，没有找到两只目标或能由官方身份资料确认的数字别名、香港映射。富途公开页面搜索也没有取得能够确认这两只全收益指数身份的行情页；新闻中提到全收益表现，不等于OpenD收录了对应标的。[富途代码格式说明](https://openapi.futunn.com/futu-api-doc/qa/quote.html)规定Python代码使用`exchange_market.symbol`，但没有提供将中证H代码转换成富途数字代码的通用规则，不能通过去掉H或猜测香港代码建立映射。

对四个候选代码逐个调用`get_stock_basicinfo`时，接口虽然返回`ret=0`，名称却均为`Unknown stock.`。这是占位响应，不能当成身份验证成功。进一步用相同的2026-10-12至2026-10-16窗口读取日K，结果如下：

| OpenD代码 | 实际日K结果 | 身份与结论 |
| --- | --- | --- |
| `SH.H00922` | `ret=-1`，`Unknown stock. H00922` | 当前不可用 |
| `SZ.H00922` | `ret=-1`，`Unknown stock. H00922` | 当前不可用 |
| `SH.H20269` | `ret=-1`，`Unknown stock. H20269` | 当前不可用 |
| `SZ.H20269` | `ret=-1`，`Unknown stock. H20269` | 当前不可用 |
| `SH.000922`（对照） | `ret=0`，5个交易日 | `CSI Dividend Index`，是中证红利价格版，不能用于分红再投资年化 |
| `SH.000888`（对照） | `ret=0`，5个交易日 | 上证全收益，有效全收益标的 |

同一连接能读取上述对照指数，目标响应又明确为`Unknown stock`，因此本次失败不应描述为`CN MarketIndixes`行情无权限。`SZ.399606`创业板全收益的有效身份和历史也已在前面的核验中确认，但不能据此假定其他全收益代码也受支持。

身份仍以中证官方单张为准：中证红利价格版为`000922`，全收益为`H00922`，净收益为`000922CNY020`；红利低波价格版为`H30269`，全收益为`H20269`，其单张未给出净收益代码。它们不能替换成上证红利、红利低波动100或港股红利指数。[中证红利官方单张](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000922factsheet.pdf)、[红利低波官方单张](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/H30269factsheet.pdf)

本次原始目录、基本信息和日线响应归档于`data/source-research/total-return-opend-dividend-2026-10-19/`，日线汇总见该目录的`history-summary.json`。未修改生产来源或数据库；两只指数继续保留中证官方全收益来源及已取得的真实数据，长历史缺口仍需明确显示。
