# IBKR 获取 VTI PE/EPS 的可行性核查

核查日期：2026-10-16。仅研究文档与本机接入条件，未修改配置、采集代码或数据库，未发出账户级 VTI 请求。

## 结论

**IBKR 当前官方支持的 API 不能作为稳定获取 VTI PE/EPS、回填历史估值的渠道。** 相关基本面入口已停用或移除；这不代表 IBKR 客户端界面也没有基本面数据。

| 路径 | 官方状态 |
| --- | --- |
| TWS `reqFundamentalData` | 10.47 移除请求、取消方法及回调，包括 Protobuf 版本 |
| TWS Fundamental Ratios | 10.47 移除 `FUNDAMENTAL_RATIOS = 47`；旧 generic tick `258` 不再是受支持的比率途径 |
| Web API PE/EPS 字段 | 2026-01-06 宣布废弃且不再通过 API 提供 |
| Web API 历史行情 | 提供 OHLC、成交量及时间，没有 PE/EPS 历史序列 |

TWS 移除清单见[官方 2026 API 版本说明的 10.47 章节](https://www.ibkrguides.com/releasenotes/prod-2026.htm)。Web API 停用公告见[官方 Changelog 的 2026-01-06 条目](https://www.interactivebrokers.com/docs/web-api/changelog)，[当前字段表](https://www.interactivebrokers.com/docs/web-api/v1/endpoints/market-data/market-data-fields)也已无 PE/EPS。[历史行情文档](https://www.interactivebrokers.com/docs/web-api/v1/endpoints/market-data/historical-market-data)返回每根 bar 的 `o/c/h/l/v/t`，不能据此回填估值。

旧教程仍可能提到 Reuters、`reqFundamentalData` 或 `258`，但[旧 TWS 文档自 2024-02-28 起停止维护](https://interactivebrokers.github.io/tws-api/guide_updates.html)。本次以 2026 年公告为准，未验证旧版本或特殊合同是否仍有残余能力。

## 验证边界与项目影响

- 未发现现成 IBKR connector 或项目适配器；本机未见 TWS/IB Gateway 标准端口 `7496/7497/4001/4002` 监听，`5000` 属于 macOS Control Center。不能由此断言用户没有账户或远端服务。
- **本次没有账户级 VTI 实测。** 未登录、购买订阅或启动客户端；文档判定不冒充接口响应。
- 保持 `ustotal` 暂停采集。即使未来取得 ETF PE，也需核实日期和持仓加权口径，不能将其等同指数 PE；口径边界见 [VTI 官方研究](vti-official-data-research-2026-10-16.md)。

目前不宜仅为取得 VTI PE/EPS 购买 IBKR 数据订阅。若官方推出替代接口，再验证 VTI 快照及历史小样本。
