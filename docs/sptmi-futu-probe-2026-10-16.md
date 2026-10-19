# S&P Total Market 的富途身份验证（2026-10-16）

## 结论

**本次未在富途确认 S&P Total Market Index 的可用标的。** 用户要求“仅使用富途；找不到则退出”，因此停止本次接入，没有添加生产配置、修改数据库、请求其他数据源，亦未以 ITOT、标普500或其他全市场指数替代。

S&P 官方的美元价格回报代码为 **SPTMI**，Reuters 代码为 **.SPTMI**；总回报代码为 SPTRTMI，不能混用。[S&P 官方指数页面](https://www.spglobal.com/spdji/tc/indices/equity/sp-total-market-index-tmi/)

## 本次验证

复用项目的 `FutuRestClient` 和本机 Keychain 中既有 `quote:read` 授权，仅调用：

```text
POST /api/v1.0/quote/stock-basicinfo
code_list = ["US..SPTMI", "US.SPTMI", "US..SPX"]
```

| 待核实代码 | 本次结果 |
| --- | --- |
| `US..SPTMI` | 未返回基本资料，未确认映射 |
| `US.SPTMI` | 未返回基本资料，未确认映射 |
| `US..SPX`（阳性对照） | 正常返回 S&P 500 Index，`stock_type=IDX`，`exchange=US` |

整批响应为 **HTTP 200 / `ret_code=0` / `success`**。富途文档说明：批量查询中无法解析的代码会被静默跳过。因此这是目标代码未被解析的结果，**不是本次授权失效或行情权限拒绝**。仅凭这些候选代码不能证明富途不存在任何其他别名，但截至本次没有已验证的可用映射。[富途基本资料接口说明](https://open.futunn.com/zh-cn/api/quote/basic-data/stock-basicinfo)

同时复核了当天 07:02:30 UTC 获取的 OpenD 美股指数目录，共537条：未匹配 `SPTMI`、`S&P Total Market`、`S&P Total Stock Market`、`标普全市场`、`標普全市場`。目录内 Wilshire 等其他全市场指数不能视为本指数。

由于身份未确认，按用户要求不再试取该指数的日线、PE或更长历史，不声称其有可用价格、PE、EPS或 band。

## 证据

- `data/source-research/sptmi-futu-probe-2026-10-16/identities.json`：本次基本资料请求、参数、HTTP 状态、观测时间和原始响应，无认证请求头及令牌。
- `data/source-research/sptmi-futu-probe-2026-10-16/summary.json`：本次结果摘要。
- `data/source-research/sptmi-futu-probe-2026-10-16/catalog-audit.json`：目录匹配条件和结果。
- `data/source-research/futu-us-index-directory-2026-10-16.json`：当天获取的完整 OpenD 指数目录。
- `data/source-research/sptmi-futu-probe-2026-10-16.py`：只读探测脚本，只有身份已确认才进入历史行情请求。

本次没有重新申请授权、扩大权限、调用账户或交易接口，也没有采集 ETF 代理。
