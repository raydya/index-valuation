# VTI 当前基准方法核实

核查日期：2026-10-16。只研究公开文件，未修改生产配置或采集数据。

VTI 现跟踪 **Morningstar US Total Market Index**，即原 CRSP US Total Market；2026-07-28的更名本身不改变指数方法。不是名称相近的 Morningstar US Market Index。[Morningstar 更名公告](https://newsroom.morningstar.com/news/news-details/2026/Morningstar-Completes-Rebrand-of-CRSP-Market-Indexes-to-Morningstar-Market-Indexes/default.aspx)

采用现行[2026年7月方法书](https://indexes.morningstar.com/api/docs/6a64c1efd7ce7b357809b99e)，[规则入口](https://indexes.morningstar.com/docs/rulebook/morningstar-market-indexes-methodology-guide)标注生效日期2026-07-28。以下页码为 PDF 印刷页码。

| 核查项目 | 现行规则摘要 | 页码 |
| --- | --- | --- |
| 范围与证券类型 | 美国可投资股票全市场；普通股、REIT可入，ADR、优先股、ETF、BDC、LP、SPAC等不入 | 7–8 |
| 美国身份 | 综合注册地、总部、EIN、美国基金持有情况、监管申报及委员会判断；并非只按上市地点 | 9–10 |
| 盈利资格 | 完整资格及可投资筛选未要求正盈利；这不代表其 PE 聚合包含亏损 | 7–12 |
| 总市值 | 新入≥1500万美元；存量低于1000万美元剔除 | 10–11 |
| 流通资格 | 新入流通比例≥12.5%，**或**流通市值≥合资格市场流通市值的0.5基点；快速IPO比例门槛10% | 10–11 |
| 流动性 | 过去125日平均日成交股数/流通股数≥0.001；存量连续两季低于0.0008剔除 | 10–11 |
| 加权 | 流通市值加权；流通因子≥10%按5个百分点取整，1%至不足10%按1个百分点，不足1%按0.1个百分点 | 13–14 |
| 定期调整 | 3/6/9/12月首个周五排名；随后五交易日渐进调整，每日约完成20%变更 | 42 |
| 新股 | 常规上市满20交易日；符合规模及可投资要求的快速IPO可在第5交易日收盘加入 | 11、51 |

两个核查边界：2026年4月已增加上述流通市值替代门槛，不能照抄2024年仅有流通比例的旧规则（第99页）；size/style之间的 banding/packeting 属于子指数迁移规则，不能套成整个 Total Market 指数统一的成分纳入条件（第6、15–20页）。

原始 PDF 保存在忽略提交的 `data/source-research/vti-methodology-2026-10-16/morningstar-market-indexes-2026-07.pdf`，同目录文本由 `pdftotext -layout` 提取。尚未用该方法生成任何成分、EPS、PE 或 band。
