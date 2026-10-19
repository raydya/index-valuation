# 海外指数分红再投资序列核验（2026-10-19）

本次为页面新增“分红再投资年化”确认独立全收益序列。现有指数价格、PE、EPS 和估值 band 的来源与计算不变；全收益序列不能用来计算 PE band。全收益采用指数原生币种的税前现金分红再投资口径，不等于某个投资者扣税、扣基金费后的实际收益。

## 已验证映射与覆盖

| 项目指数 | 全收益身份 | 可用来源 | 本次实际覆盖 | 结论 |
| --- | --- | --- | --- | --- |
| 纳斯达克100 | Nasdaq-100 Total Return，`XNDX`，USD，gross total return | Nasdaq 官方公开历史页接口 | 1999-03-04—2026-10-16，原始6,950条、有效6,949条；同日短窗口与完整返回一致 | 已接入独立全收益序列，1条休市日零值隔离 |
| 恒生科技 | Hang Seng TECH Index (Gross Total Return Index)，供应商代码 `HSTECHT`，官网内部代码 `12083.00`，HKD | 恒生官网公开数据接口 | 2021-10-15—2026-10-16，1,231条；当前约5年滚动窗口 | 可接入，但更早区间明确缺失 |
| 标普500 | S&P 500 Total Return，`SPTR` / `SPXT`（分发商标识不同），USD | 身份有官方出处，本次未验证可用自动日度接口 | 未取得可用历史序列 | 显示数据尚不可用；不能拿价格指数或 ETF 替代 |
| 美国全市场 | 现有 VTI 基准相关序列 | 保留项目暂停采集状态 | 未新增采集 | 不恢复 Yahoo，不用另一指数或 ETF 替代 |

Nasdaq 官方[指数版本表](https://indexes.nasdaq.com/docs/Index_Versions_NDX.pdf)将 `XNDX` 明确列为 gross total return，现金分红在除息日再投资；`XNDXNNR` 是按30%假定税率扣减后的另一个版本，不能混用。该表列出 XNDX 日期为1999-03-04；不能因为 NDX 价格指数1985年存在就补造更早全收益历史。[官方 XNDX 页面](https://indexes.nasdaq.com/Index/Overview/XNDX)及[FRED 的 Nasdaq 来源说明](https://fred.stlouisfed.org/series/NASDAQXNDX)交叉确认身份。

恒生[TECH 官方页面](https://www.hsi.com.hk/en-hk/indexes/all-indexes/index-details/?seriesCode=hstech)的同一系列接口同时给出价格 `02083.00`、gross TRI `12083.00` 和 net TRI `22083.00`。2026-10-16三者分别为4108.24、4626.76、4623.14，不能互换。[恒生全收益说明](https://www.hsi.com.hk/index-resources/total-return-index-series/)说明将现金分红按指数权重再投资；[官方方法资料](https://www.hsi.com.hk/static/uploads/contents/en/dl_centre/methodologies/IM_hstech_TBE.pdf)列出 `.HSTECHT` / `HSTECHT` 为 Gross TRI、`.HSTECHN` / `HSTECHN` 为 Net TRI。本机直接下载旧方法链接失败，代码身份采用本次成功取得的官方系列响应作为运行证据，不把搜索缓存当成实际下载。

标普[官方指数页面](https://www.spglobal.com/spdji/en/indices/equity/sp-500/)区分 Price Return、Total Return、Net Total Return；[官方指数数学说明](https://www.spglobal.com/spdji/en/methodology/article/index-mathematics-methodology/)说明总收益包含价格变化与股息再投资。[官方资料](https://www.spglobal.com/spdji/en/documents/additional-material/sp-sector-indices.pdf)列 `SPTR`，另一份[官方方法](https://www.spglobal.com/spdji/en/documents/methodologies/methodology-sp-500-dynamic-veqtor.pdf)使用 `SPXT` 标识 S&P 500 Total Return 收盘值；这些标识不自动意味着富途支持。

## Nasdaq 接口与校验

[官方历史页](https://indexes.nasdaq.com/Index/History/XNDX)加载的 `Scripts/History.js?v=2.0.0.0` 明确调用以下无登录历史接口：

```text
POST https://indexes.nasdaq.com/Index/HistoryChartData
Content-Type: application/x-www-form-urlencoded
id=XNDX&startDate=1999-03-04&endDate=2026-10-16
```

返回 JSON 数组，每行有 `FPSymbol="XNDX"`、`x`（UTC午夜毫秒编码的交易日）、`y`（指数收盘值），另有 Open、High、Low 等字段。`x` 是日期标签，不能转换为纽约时区后取日期，否则会错误地前移一天。2026-10-16的完整小数为35675.89765529041。

另一次短窗口 `POST /Index/HistoryData` 使用 `id=XNDX&startDate=2026-10-08&endDate=2026-10-16&timeOfDay=EOD`，返回7条，`TradeDate` 与 `Value` 逐日对应上面的图表接口。两种响应是同一官方上游，只属于接口一致性验证，不能宣称独立数据源验证。

完整返回6,950个不同日期，无周末记录；其中2012-10-29是市场日历中的休市日，供应商值为 `0E-11`，按无效观测逐行隔离，保留同年其他有效值。实际入库6,949条，与独立交易日历比对无缺少交易日。生产适配器按自然年分窗，每次最多366个日历日，避免依赖长区间图表是否采样或静默截断；仍由采集编排核验实际交易日日历及覆盖，不把“HTTP 200”当成无缺口保证。所有值以 Decimal 解析，校验身份、UTC日期、请求范围、重复、有效正值及已收盘时刻；JSONB中的原始小数使用精确字符串，原始响应文本完整归档。代码为 `providers/nasdaq_returns.py`，不改原 Nasdaq 价格适配器。

`api.nasdaq.com/api/quote/XNDX/historical` 在本次返回 `Symbol not exists`，与上述可用网站历史接口不同。FRED 公共序列 `NASDAQXNDX` 身份与1999-03-04起点已确认，但本机本次 CSV、TXT 下载连续超时，不能写成该序列不支持，也不作为当前主采集路径。

## 恒生接口与覆盖边界

身份与当日值：

```text
GET https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy/v1/product-data/pub/series/metadata/v2
?data=values&seriesCode=hstech&language=en-hk
```

全收益历史：

```text
GET https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy/v1/product-data/pub/indexes/metadata/v2
?data=valueHistoryPub&indexCodes=12083.00&language=en-hk
```

历史响应 `code="0"`，`data` 恰有一个对象，包含明确 `indexCode`、Gross Total Return 的 `indexName` 及 `valueHistory=[{"date":"YYYY-MM-DD","close":"..."}]`。实际首日2021-10-15，末日2026-10-16；末值与系列接口同日一致。

该公开接口未暴露日期分页参数，只返回当前滚动窗口。适配器每次请求一次，核验所有日期、重复和正值，再过滤所需日期；请求早于返回首日时明确记录历史覆盖不足。当前窗口起点不是指数成立日，也不能据此声称取得2014-12-31起的完整历史。代码为 `providers/hsi_returns.py`。

富途 OpenD 当日港股指数目录203条，有恒生指数及国企指数的 gross/net TRI，但没有恒生科技非杠杆 gross TRI。`HK.800860` 是 **HSTECH TR 2x Leveraged Index**，不用于替代。当前恒生科技价格/PE继续使用原 OpenD 配置。

## 富途 REST 与暂停指数

本次只读 `stock-basicinfo` 同时查询 `US..SPTR`、`US..SPXT`、`US..SP500TR`、`US..XNDX` 和控制组 `US..SPX`，HTTP200、ret_code=0，仅返回控制组标普500价格指数。现有537条美国指数目录亦无对应候选。说明这些候选未被此次证券查询确认，不能推断富途永远不提供任何全收益指数。

标普官网直接 HTTP 请求本次为403，未取得可靠日度总收益历史；按来源真实限制记录，不使用现有价格点位加当前股息率估算。美国全市场继续遵守 `collection_enabled=false`；本次未读取账户或交易数据、未启用ETF代理、未修改其原价估值来源。

## 证据与验证

原始证据目录：`data/source-research/total-return-overseas-2026-10-19/`（研究文件按项目策略忽略，不提交凭据）。主要文件：

- `futorest-identities.json`：REST候选与SPX控制组响应。
- `futu-hk-index-directory.json`：港股指数完整目录。
- `nasdaq-history-page.txt`、`nasdaq-history-js.txt`：官方接口出处。
- `nasdaq-history-chart.txt`、`nasdaq-history-data.txt` 及对应metadata：全历史与短窗口官方响应。
- `hsi-react.txt`：恒生官网公开客户端里的接口出处。
- `hsi-series-values.json`、`hsi-gross-history.json` 及对应metadata：恒生身份、gross/net区分与历史。
- `adapter-smoke.json`、`smoke-raw/`：新适配器的实际短区间只读验证，无生产数据库写入。

适配器测试覆盖gross/net/价格身份混淆、UTC日期前移、未收盘数据、重复/错误/非正数、年度窗口及滚动历史缺口；测试和格式检查实际运行，主任务另行完成生产采集、数据库和页面验证。
