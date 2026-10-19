# 长桥公开网页、ETF 文件与组合估值路径复核

核验日期：2026-10-16。范围：VTI、VOO、QQQ。仅使用公开 HTML、网页直接引用的 JavaScript、文件链接和官方文档；未使用浏览器、登录凭据、账户或交易接口，未联系供应商。原始证据在 `data/source-research/longbridge-public-paths-2026-10-16/`；`public_summary.json` 保存结果与 SHA-256。

## 结论

本次发现长桥已有两种不同的上游：**通用估值页面注明 S&P Global 财务数据；ETF 持仓、风格和基金文件注明 Morningstar 数据**。公开 `/valuation` 能读取与已有 OpenAPI 相同的异常长期常量序列，换到网页不能解决它的口径或停更问题。但沿 `/wiki` 实际文件链接取得了 Vanguard 发行人 Factsheet，得到 **2026-03-31 的 VTI 组合 PE 25.4x、VOO 组合 PE 26.1x**。这是有真实有效日的发行人单点资料，不是已获得稳定日度历史 PE。两份 PDF 未完整说明 PE 期间、加权和负盈利处理，仍不能直接接入生产 band。

继续向长桥确认的具体产品应是 **Morningstar ETF portfolio characteristics 中的组合 PE 及其历史**，而非再次要求开通普通股票财务 EPS。这是有其现存数据供应链支持的询问方向，尚无公开证据保证长桥已经授权或开放这一字段。

## 公开估值页复核

以下页面均直接 HTTP GET 成功；HTML 内含 `window.__TANSTACK_DEHYDRATED__.queries.push(...)` 的 JSON。只用 JSON 解析器读取，没有执行页面脚本。该查询的 key 为 `['valuation', 'ETF/US/<symbol>', 'detail', 'pe']`。

| 官方页面 | 页面所示 PE / 盈利分母 `part` | 实际历史窗口 | 与已有 OpenAPI 对照 |
| --- | --- | --- | --- |
| [VTI](https://longbridge.com/en/quote/VTI.US/valuation) | 9.74x / 38.8597 | 156 周点，2023-10-23—2026-10-12 | 全部日期及原值相同 |
| [VOO](https://longbridge.com/en/quote/VOO.US/valuation) | 7.88x / 90.0556 | 261 周点，2021-10-18—2026-10-12 | 全部日期及原值相同 |
| [QQQ](https://longbridge.com/en/quote/QQQ.US/valuation) | -4.94x / -147.4293 | 261 周点，2021-10-18—2026-10-12 | 全部日期及原值相同 |

三者末尾均为 131 个完全相同的周值，自 2024-04-15 至 2026-10-12。页面 overview 日期为 2026-10-15；它不证明历史 PE 或分母财务数据于该日更新。页面 `part` 与行情 JSON 的 `eps_ttm` 不是同一数值，不能混合取用或据当前价格回算成历史 EPS。

日期用 `America/New_York` 解码原始秒时间戳，未移动非交易日。网页与 OpenAPI 同值只证明两个出口返回同一序列，不构成独立上游核验。

实际前端代码调用以下路径，参数来自已见调用，未猜测其他路由：

```text
GET /stock-info/valuation-detail?counter_id=ETF/US/VTI&indicator=pe
GET /stock-info/history-valuation?counter_id=ETF/US/VTI&indicator=pe&range=...
```

这里只记录网站相对路径，不将其当作无需授权的正式 OpenAPI；实测访问入口是上述完整公开网页。证据为页面直接引用的 [app-initial-CkxaiUOM.js](https://assets.wbrks.com/web/longbridge-com/apps/discover/assets/app-initial-CkxaiUOM.js) 和 [valuation-cBjxJva9.js](https://assets.wbrks.com/web/longbridge-com/apps/discover/assets/valuation-cBjxJva9.js)。

[公开语言资源 en-BTFNljPF.js](https://assets.wbrks.com/web/longbridge-com/apps/discover/assets/en-BTFNljPF.js) 的 `valuation_tip_03` 将通用 PE 定义为股价除 TTM 每股盈利；`valuation_disclaimer_content` 标注财务数据来自 S&P Global。该文字没有给出 ETF 持仓汇总方法，也不能推翻实际观测的长期常量问题。QQQ 旧行情 EPS 与基金会计损益的对应证据见 [ETF 会计 EPS 核验](etf-accounting-eps-validation-2026-10-16.md)。

## Morningstar ETF 数据目前实际公开了什么

[VTI Composition](https://longbridge.com/en/quote/VTI.US/holdings) 的 HTML 实际提供 2026-08-31 的资产、行业、地区分布和 Top 10 持仓，十项权重合计 0.3345134。每个持仓包含代码、权重、行业和风格信息，响应没有 PE、EPS 字段；十项持仓不足以重算全基金估值。网页请求为 `GET /v2/stock-info/asset-allocation?counter_id=ETF/US/VTI`，没有已见的历史日期参数。

[VTI Ratings](https://longbridge.com/en/quote/VTI.US/ratings) 返回 Morningstar 评级、九宫格风格及分布：评级日期 2026-09-30，风格分布日期 2026-08-31；没有组合 PE/EPS。上述语言资源的 `etf_wiki_composition_tip`、`etf_wiki_holdings_tip`、`etf_wiki_style_box_tip` 明确 Morningstar 是持仓/权重/风格上游；持仓个股行业分类则注明 S&P Global。

[VTI Wiki](https://longbridge.com/en/quote/VTI.US/wiki) 返回 ETF 基本资料和基金文件清单；本次基本资料的 `benchmark` 为 `Morningstar US Total Market TR USD`，`underlying_counter_id` 为空。下文较早 Factsheet 使用 CRSP 基准，必须保留各自有效日；不能把第三方百科标签或空代码转换成已经验证的历史指数映射。

## 发行人 Factsheet：本轮可用的新证据

公开百科 [VTI](https://longbridge.com/en/quote/VTI.US/wiki)、[VOO](https://longbridge.com/en/quote/VOO.US/wiki)、[QQQ](https://longbridge.com/en/quote/QQQ.US/wiki) 的文件列表分别只有 4、4、3 项。VTI 与 VOO 为年报、半年报、招募书及一份 Factsheet；QQQ 没有 Factsheet，只有前三种报告。

网页调用 `GET /v1/stock-info/etf-files`，只传 `counter_id`。实际 [wiki-BZxWG7Gl.js](https://assets.wbrks.com/web/longbridge-com/apps/discover/assets/wiki-BZxWG7Gl.js) 直接遍历整个 `files` 数组，没有日期、页码、offset 或游标参数。当前已见路径不能证明存在历史 Factsheet 档案；本轮没有尝试猜测参数或文档哈希。

`etf_wiki_documents_tip` 标注基金文件经 Morningstar 提供，页面的更新日期是平台取回文件的日期。以下 PDF 数据日期与其平台更新时间因此分开保存：

| 文件与页码 | PDF 的有效日 | ETF PE | 文件所列基准 PE | 页面文件更新时间 |
| --- | --- | --- | --- | --- |
| [Vanguard VTI Factsheet](https://fundamental.longbridge-inc.com/Document/b047bd9e0f77b2e95377aa18cb994483.msdoc)，第 2 页 | 2026-03-31 | 25.4x | CRSP US Total Market Index，25.4x | 原值 `06/02/2026` |
| [Vanguard VOO Factsheet](https://fundamental.longbridge-inc.com/Document/2fad3f29702edd2b92ab3e34c3dd4e59.msdoc)，第 2 页 | 2026-03-31 | 26.1x | S&P 500 Index，26.1x | 原值 `06/02/2026` |

两份文件均 HTTP 200、真实 PDF，已提取完整文本并渲染检查第 2 页。PE 出现在 ETF attributes 表，分别并列基金与基准；这里是发行人对组合特征的披露，与基金自身会计损益分母不同。**两页 PDF 没有给出该 PE 的 TTM/forward、加权公式或负盈利处理定义**；截至本轮证据只能标为“发行人披露 PE，细化方法待核实”，不能按供应商通用股票 TTM 定义补写。

QQQ 的[半年度报告](https://fundamental.longbridge-inc.com/Document/56cb59f1f540702a700d624b9a68645c.msdoc)也实际下载并提取全文，报告日 2026-03-31，内容为投资明细与基金财务报表，未找到组合 PE。没有使用报告中的投资损益来替代组合盈利。

研究导出为 [issuer-pe-observations.csv](../data/exports/longbridge-issuer-factsheet-pe-research-2026-10-16/issuer-pe-observations.csv)；4 行表示 2 只 ETF 和文件列示的 2 个基准，**只有一个有效日期**。记录原始 URL、文件 SHA-256、页码、观测时间和尚未核实的细化口径，未推导 EPS、未补日度序列、未入生产数据库。一个文件的 ETF/基准两列不视为两个独立校验来源。

## 支持渠道与付费边界

[Longbridge 官方 Getting Started 的 Contact & Feedback](https://open.longbridge.com/docs/getting-started#contact--feedback)明确引导在 [longbridge/developers GitHub Issues](https://github.com/longbridge/developers/issues) 提问。这是本轮找到的直接 OpenAPI 技术反馈入口。若需要非公开讨论账户授权或供应商许可，可由用户通过其开户实体的客服转交数据产品团队；本轮未代发邮件或 issue。

[官方通用 FAQ](https://open.longbridge.com/docs/qa/general)说明 OpenAPI 接口访问不额外收费，高级行情权限仍可能需要购买；文档没有声称购买美股全市场行情、港股 L2 或增加资产即可获得 Morningstar ETF 历史组合 PE。**没有找到这一估值字段单独付费开通、授权范围、更新 SLA 或历史起点的官方产品承诺**。不应为本问题先买无关行情包。

下列文案已准备，但未发送：

> 我们在开发只读指数估值系统，希望取得 VTI、VOO、QQQ 的持仓公司盈利口径 PE（ETF portfolio price/earnings ratio），及其有效日期和历史序列，不需要交易或账户资产接口。
>
> 当前 `/v1/quote/valuation` 的 PE 历史和官网 `/valuation` 返回一致；三只 ETF 自 2024-04-15 起连续 131 周分别固定为 9.74、7.88、-4.94，截至样本日 2026-10-12。网页同时展示 2026-10-15 的现价，故需要确认该 PE 数据是否停止更新、被补值，以及财务期间和字段含义。
>
> 官网估值模块注明 S&P Global 财务来源，ETF 持仓/风格/文件注明 Morningstar 来源。经 `/wiki` 的实际文件链接取得 Vanguard 2026-03-31 Factsheet，VTI 组合 PE 为 25.4、VOO 为 26.1。请问贵司已获授权的 Morningstar ETF 数据中，是否另有 portfolio characteristics 的 trailing/forward PE、历史 PE、盈利收益率或组合 EPS 可供 OpenAPI/数据产品使用？若有，请给出正式接口、字段、需要开通的具体产品及费用；若无，请明确说明。
>
> 还请确认有效日、历史起点与采样频率、更新周期、是否保存当时可得数据/后续修订、持仓权重方法、负盈利和无盈利持仓的处理。是否支持历期 Factsheet 文件索引？现有 `/v1/stock-info/etf-files` 的公开调用只显示各类型单份文件，未见日期/分页参数。仅 Top 10 持仓不足以替代全组合估值，请勿以普通股票财报 EPS 或行情包代替此需求。

接口和网页分别有不同维护周期，上述 hash 资产地址用于保存本次证据，不应写死为长期生产依赖。后续拿到正式产品答复或新实际响应后，再按照项目来源接入流程核实，不能从本次单点资料推断“长桥已有完整历史组合 PE”。
