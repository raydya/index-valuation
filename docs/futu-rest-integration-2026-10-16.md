# 美股指数迁移到富途 REST（2026-10-16）

标普500、纳斯达克100的新增采集改用富途 REST 的指数点位和指数 PE。Yahoo 价格及 ETF 代理 PE 停止新增采集，旧原始数据、方法和导出保留。内地、香港的 OpenD 配置不变。

## 身份、来源和计算

| 项目 | 标普500 | 纳斯达克100 |
| --- | --- | --- |
| 关联 ETF，仅作基准说明 | VOO | QQQ |
| REST 证券代码 | `US..SPX` | `US..NDX` |
| 身份验证 | US / IDX / S&P 500 | US / IDX / Nasdaq 100 |
| 采用的价格与估值 | 富途 REST | 富途 REST |
| 同日价格核验 | 标普官方数据，经 FRED 分发 | Nasdaq 官方接口 |
| 方法 | `futorest_index_implied_daily_v1` | 同左 |

适配器复用同一实现，先查询证券身份，再读取历史日 K 和独立估值详情。K 线中的 `pe_ratio` 实际为0，不用于估值；PE/PB/PS 分别来自 `valuation_type` 1/2/3。API 返回的指数 PE 未完整披露盈利期间、加权及亏损处理，不将其称为已核实的 TTM PE。

隐含 EPS = 同日 REST 收盘点位 / REST PE；band = 隐含 EPS × 倍数，保留8倍。计算使用 Decimal；JSONB 原始字段以可复核的字符串保留小数精度。价格核验沿用原门槛：≤1%通过、1%—5%警告、>5%重大差异。缺少同日价格对照或存在重大差异时，保留点位与 PE，EPS/band 留空；缺失 REST 点位不以核验源替补。

没有已确认同口径的独立 PE 来源，PE 对照状态仍为 unconfirmed。均值和分位只使用本方法过去的有效正 PE，至少252个日度观测；不混入旧 Yahoo ETF 代理、雪球历史或旧恐慌阈值。

## 接口实测与历史边界

接口文档：[历史日 K](https://open.futunn.com/zh-cn/api/quote/basic-data/history-kline)、[估值详情](https://open.futunn.com/zh-cn/api/quote/valuation/detail)、[证券信息](https://open.futunn.com/zh-cn/api/quote/basic-data/stock-basicinfo)。公开页面工具无法直接读取时，使用官方工具 schema 和实际只读请求核实字段。

- `interval_type=10` 为30年窗口。2026-10-16试取的 PE 日期为1996-10-17至2026-10-15；标普500返回7,397条，纳指100返回7,545条。记录为日度，但来源仍有缺口，不能称为30年无缺口。
- 跨多年日 K 请求即使传 `num=370`，也可能只返回最早1,000条并声称 `has_more=false`。因此适配器按自然年分段，每段最多366个自然日，并验证重复、越界、日期、纽约时区与收盘状态。
- 全量请求实测会返回 HTTP200、`ret_code=-11` 的限流响应。适配器对普通请求间隔至少1秒，限流时遵守 `Retry-After` 或服务器消息中的等待时间，同一请求最多4次；最终失败保留成功区间和错误归档。长等待要求超过60秒时停止本次重试，不提前请求。
- 1970年之前的日 K 参数实际返回 invalid parameter。该范围报告为不支持；空年份继续向后采集，不将空响应视为全部历史结束。
- 日期采用纽约时区。K 线时间戳单位毫秒、估值时间戳单位秒；验证其与供应商日期相同。K 线 `time_zone` 实测为小时偏移，适配器兼容文档描述的分钟偏移并按 IANA 时区核验 DST。
- 历史数据来源缺口保留为空，不复制当前 PE 到过去，不前向填充。

FRED 的标普数据按[官方说明](https://fred.stlouisfed.org/series/SP500)仅提供滚动近10年。完全落在覆盖之前的请求实测会忽略日期参数并返回整个当前窗口；适配器现在先裁剪请求范围、明确记录更早覆盖限制，继续严格拒绝越界或重复响应。单片错误不再丢弃其他已成功区间。Nasdaq-100不适用该10年限制。

证据保存在 `data/source-research/futu-rest-long-history-probe-2026-10-16.json`、`futu-rest-chunk-probe-2026-10-16.json`、`futu-rest-provider-smoke-2026-10-16.json`，响应按内容哈希存于 `data/raw/`。

## 实际入库与缺口

截至本次回填及增量验证（2026-10-16），两只指数最新已收盘数据均为2026-10-15：

| 数据 | 标普500 | 纳斯达克100 |
| --- | --- | --- |
| REST 收盘点位 | 8,806条，1990-12-19起 | 9,264条，1990-01-02起 |
| REST PE | 7,397条，1996-10-17起 | 7,545条，1996-10-17起 |
| REST PB / PS | 各7,397条 | 各7,545条 |
| 可计算 EPS / band | 2,513条，2016-10-17起 | 7,523条，1996-10-17起 |
| 最新点位 / PE | 7,744.06 / 25.858 | 29,935.5947 / 32.447 |
| 最新隐含 EPS | 299.4841054992652177 | 922.5997688538231578 |

上述为实际有记录的起止范围，**不表示自成立以来或该范围内无缺口**：

- 标普在首末记录之间缺214个交易日点位，PE缺149日，集中于1991—2004年；REST另有26条价格日期落在纽约交易日历之外，已隔离，不能把周日记录移动到周五。更早历史暂未返回；1970年前参数不受支持。
- 纳指在首末记录之间仅1999-09-24缺价格和 PE。另有22个具有 PE 的日期，REST与Nasdaq价格差异超过5%，故这22日不生成 EPS/band；主要在1996年及2002年，保留双方原值。全部价格记录的核验分布为7,483通过、80警告、75重大差异、1,626缺对照，其中部分早期差异日尚无 PE。
- 标普2016年以前没有当前允许使用的同日官方价格对照，因此即使 REST 有价格与 PE，也保留 EPS/band 空值；PE 历史本身仍参与同方法统计。没有重新启用旧 Yahoo 历史补空。
- Nasdaq 官方响应有179条日历外价格记录，已隔离；恢复2002年以后区间时再次遇到的128条是该集合的子集，不计为新增179+128条问题。最终派生序列没有日历外日期。
- 针对标普1991年4月、2004年10月及纳指1999-09-24进行了小窗口复查：仍返回异常周日日期或空记录，确认不是年度请求上限造成；保留原始证据，不补值。

覆盖与质量审计：`data/source-research/futu-rest-runtime-coverage-2026-10-16.json`；缺口复查：`futu-rest-gap-recheck-2026-10-16.json`。完整/恢复回填报告在 `data/exports/sp500-futorest-full-2026-10-16.json`、`nasdaq100-futorest-full-2026-10-16.json`、`nasdaq100-futorest-resume-2026-10-16.json`，报告再指向详细隔离清单。

## 美国全市场的暂停

VTI 基准 Morningstar US Total Market（原 CRSP US Total Market）暂未找到已验证的富途映射。OpenD 的537只美国指数目录中没有匹配；REST 证券查询对 `US.CRSPTM1`、`US..CRSPTM1` 均返回 `ret_code=-7 / invalid_symbol`。这说明候选代码无效，不据此声称富途永久不可能提供该指数。

配置 `collection_enabled=false`，更新与回填保存 blocked 报告并继续其他指数，不构造 Yahoo 等旧采集器。网页/API 输出暂停原因，旧方法历史只读可查。Wilshire 5000 与 VTI 基准不同，不自动替换。证据：`data/source-research/futu-rest-ustotal-identity-2026-10-16.json`、`futu-us-index-directory-2026-10-16.json`。

## 授权与每日更新

OAuth 与 OpenD 登录独立。`scripts/auth_futu_rest.py` 使用 PKCE S256、state 和本机回调完成用户只读授权，令牌保存至 macOS Keychain，服务名 `cn.index-valuation.futu-rest`；只允许 `quote:read`，不申请交易权限。授权后已真实验证 refresh token 续期及行情查询。

采集客户端在到期前自动续期，并用本地进程锁保护读取、刷新和写回；HTTP401只续期重试一次。刷新响应缺少新 refresh token 时保留原值，失败不打印上游响应体或凭据，不自动回退 Yahoo。使用白名单行情端点，默认禁止重定向。

```bash
uv run --locked python scripts/auth_futu_rest.py --status
# 仅在缺失或失效时重新执行交互授权
uv run --locked python scripts/auth_futu_rest.py
uv run --locked index-valuation update-index --market US
```

每天北京时间09:00的既有 launchd 任务继续执行同一更新入口，自动使用新配置，无需重新安装。Keychain 需对运行任务的用户可访问。增量保留14个自然日重叠，upsert 按来源和日期保存，再重建当前方法。美国全市场暂停会使整个批次退出码为2，须查看各指数报告，而非将此解释为其他指数均失败。

## 验证结果

- 已在真实 launchd 环境手动触发原09:00美股任务：标普500、纳指100均为 collected，美国全市场为明确的 blocked；任务退出2符合暂停策略。实际请求仅富途 REST、FRED 和 Nasdaq，没有 Yahoo。
- 调度前后逐来源数量及价格/估值数值指纹一致，重叠更新无重复；旧来源的采集时间和值未改变。审计保存在 `data/source-research/futu-rest-before-daily-2026-10-16.json` 与 `futu-rest-after-daily-2026-10-16.json`。
- 363项非数据库测试、20项 PostgreSQL 集成测试通过；覆盖响应数字到 JSONB、Decimal 精度、OAuth刷新和跨客户端锁、只读端点限制、年度分段、限流重试、旧方法隔离、暂停任务继续批次、API/CSV。
- Ruff、格式检查、JavaScript语法、diff检查通过。重启网页加载新配置后，项目 WebKit 验证全部指数切换、8倍/均值/分位 band、历史回看、CSV、手机布局和无脚本错误；新增断言确认 REST 价格与 PE 来源、不混入 Yahoo、旧恐慌阈值不画线、暂停状态可见。报告：`data/exports/futorest-web-validation-2026-10-16.txt`。
