'use strict';

const $ = id => document.getElementById(id);
const state = { index: new URLSearchParams(location.search).get('index'), chartYears: 5, historyYears: 5, asOf: null, mode: 'fixed', data: null, page: 0, hover: -1, request: null };
const colors = ['#258c88', '#469ca3', '#467bb5', '#706ea9', '#a08b6c', '#b47628'];
const meanColor = '#b54d64';
const panicColor = '#18875b';
const greedColor = '#c44336';
const sourceNames = { futu: '富途 OpenD', futorest: '富途 REST', csindex: '中证指数', cnindex: '国证指数', fred: 'FRED（官方数据分发）', nasdaq: 'Nasdaq 官方', yahoo: 'Yahoo Finance', google: 'Google Finance', yahooetf: 'Yahoo ETF 代理', baostock: 'BaoStock', eastmoney: '东方财富', danjuan: '雪球基金', multpl: 'Multpl', gurufocus: 'GuruFocus' };
const peBasisNames = { multpl_reported_ttm_current_estimate: '过去12个月已报告盈利，当前 PE 为估算', gurufocus_published_pe_unconfirmed: '供应商指数 PE；盈利、加权及亏损处理未完整确认', csindex_published_rolling_pe: '中证发布的滚动 PE', danjuan_published_pe_ttm: '雪球基金发布的 PE TTM；与官方计算用股本口径分别保存' };
const num = value => value == null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
const fmt = (value, digits = 2) => num(value) == null ? '—' : num(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const percent = value => num(value) == null ? '—' : `${fmt(value, 1)}%`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const windowName = () => state.historyYears ? `近${state.historyYears}年` : '全部历史';
const setText = (id, text) => { $(id).textContent = text; };
const QUALITY = { pass: ['≤1%', ''], warning: ['1–5% 差异', 'warning'], error: ['>5% 差异', 'error'], missing: ['暂无对照', 'missing'] };
const PANIC_STATUS = { reference: '参考线可用', insufficient_history: 'PE 历史不足5年', missing_pe: '当日缺少有效 PE', collection_paused: '已暂停采集', unusable_pe: '当日 PE 不可用', different_basis: 'PE 口径不一致', different_method: '估值方法不一致', not_calculated: '尚未计算' };

function rememberIndex() {
  const url = new URL(location.href);
  url.searchParams.set('index', state.index);
  history.replaceState(null, '', url);
}

function parameters(all = false) {
  const params = new URLSearchParams({ chart_years: all ? 0 : state.chartYears, history_years: state.historyYears });
  if (state.asOf) params.set('as_of', state.asOf);
  return params;
}

async function load() {
  state.request?.abort();
  const request = new AbortController();
  state.request = request;
  $('loading').hidden = false;
  $('chart-wrap').setAttribute('aria-busy', 'true');
  $('export').removeAttribute('href');
  $('export').setAttribute('aria-disabled', 'true');
  $('error').hidden = true;
  $('panic-live-section').hidden = true;
  $('panic-section').hidden = true;
  try {
    const response = await fetch(`/api/indices/${encodeURIComponent(state.index)}/dashboard?${parameters()}`, { signal: request.signal });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '参数无效，请调整日期或区间。');
    if (request !== state.request) return;
    state.data = data;
    state.page = 0;
    state.hover = -1;
    render();
  } catch (error) {
    if (error.name === 'AbortError') return;
    setText('error-text', error.message || '读取失败，请检查本地服务。');
    $('error').hidden = false;
  } finally {
    if (request === state.request) {
      $('loading').hidden = true;
      $('chart-wrap').setAttribute('aria-busy', 'false');
    }
  }
}

function render() {
  const d = state.data, p = d.selected;
  const proxy = d.index.valuation.proxy_symbol;
  const paused = d.index.collection_enabled === false;
  document.title = `${d.index.name} · 指数估值`;
  setText('index-title', d.index.name);
  setText('code', d.index.code);
  setText('benchmark-note', d.index.description || '');
  $('benchmark-note').hidden = !d.index.description;
  setText('pe-source-label', `${paused ? '历史归档 · ' : ''}${d.index.valuation.label}`);
  setText('pe-title', proxy ? 'ETF 代理 PE' : '指数 PE');
  setText('chart-description', proxy ? `用 ${proxy} 代理 PE 换算不同估值倍数下的指数点位。` : '把每一天的盈利，放回不同的估值水平。');
  setText('eps-title', proxy ? '代理隐含 EPS' : '隐含 EPS');
  setText('eps-unit', proxy ? '按 ETF 代理 PE 换算的指数点数' : '指数盈利点数');
  $('eps-help').title = proxy ? `指数点位 ÷ Yahoo ${proxy} PE；为代理估值换算，不代表已核实的指数实际盈利。` : '同日指数点位 ÷ 主源指数 PE；单位为指数点，盈利期间跟随 PE 口径';
  setText('valuation-source-label', d.index.valuation.source === 'futorest' ? '富途 REST 口径' : d.index.futu_supported ? '富途口径' : '暂无 PB / PS');
  setText('price', fmt(p.price));
  setText('pe', fmt(p.pe, 3));
  $('pe').classList.toggle('error', p.pe_usable === false);
  setText('mean-summary', `${windowName()}平均 ${p.mean_pe == null ? '—' : `${fmt(p.mean_pe, 2)}×`}`);
  setText('eps', fmt(p.implied_eps));
  setText('pbps', `${fmt(p.pb, 3)} / ${fmt(p.ps, 3)}`);
  setText('price-source', `${sourceNames[p.price_source] || '待补数据'} · ${p.trade_date}`);
  renderPriceReturn();
  setText('percentile-label', `${windowName()} PE 分位`);
  setText('percentile', percent(p.percentile));
  setText('history-count', `${fmt(p.history_count, 0)} 个${d.index.valuation.history_frequency === 'weekly' ? '周' : '历史'}观测${p.history_count < d.minimum_history ? '，样本不足' : p.history_span_short ? `，历史不足${d.history_years}年` : ''}`);
  setText('data-stamp', `${paused ? '暂停采集 · 历史截至' : '数据截至'} ${d.available_end}`);
  $('as-of').min = d.available_start;
  $('as-of').max = d.available_end;
  $('as-of').value = p.trade_date;
  $('export').href = `/api/indices/${encodeURIComponent(state.index)}/export.csv?${parameters()}`;
  $('export').removeAttribute('aria-disabled');
  setText('table-description', `当前区间 ${fmt(d.series.length, 0)} 个交易日，点击日期回看估值`);
  setText('table-range', `${d.series[0]?.trade_date || '—'} 至 ${p.trade_date}`);
  const waiting = p.pe == null ? '当日指数 PE 暂缺，隐含 EPS 和点位 band 暂不生成。 ' : p.pe_usable === false ? '当日 PE 存在重大来源差异，EPS 和点位 band 暂不生成，该 PE 不计入历史均值。 ' : p.implied_eps == null ? '当日价格尚未通过交叉核验，EPS 和点位 band 暂不生成。 ' : p.reference_pe == null ? '当日 PE 暂无独立来源对照，估值带为主源口径参考计算。 ' : '';
  const difference = num(p.pe_relative_error) > .01 ? `${sourceNames[p.pe_source] || '主源'} PE ${fmt(p.pe, 3)}×，${sourceNames[p.reference_pe_source] || '独立参考'} PE ${fmt(p.reference_pe, 2)}×，数值相差 ${percent(num(p.pe_relative_error) * 100)}。 ` : '';
  const sourceNote = proxy ? `采用 Yahoo ${proxy} ETF 代理 PE，非指数官方估值；按 ETF 最近已收盘行情日关联快照，PE 自身生效日未提供。历史独立积累，旧口径恐慌线不适用。` : d.index.valuation.source === 'csindex' ? '以中证指数官方数据为准；第三方差异保留提示，不影响官方口径的 EPS、估值带和历史统计。' : d.index.valuation.source === 'none' ? '当前展示指数价格；富途 OpenD 不支持该指数估值。' : `${d.index.valuation.label}的加权和亏损处理尚未完整核实；对照 PE 保留各自口径。`;
  const priceNote = p.price_status === 'unverified' ? (p.price_usable ? '采用官方点位，暂无独立价格对照。 ' : '当日点位仅有一个来源，待交叉核验。 ') : p.price_status === 'error' && p.price_usable ? '点位存在来源差异，采用指数官方点位。 ' : '';
  const usableLink = p.implied_eps == null && d.latest_complete_date && d.latest_complete_date !== p.trade_date ? `<button type="button" id="last-usable">查看 ${esc(d.latest_complete_date)} 的参考 band</button> ` : '';
  const pauseNote = paused ? `【暂停新增采集】${d.index.collection_disabled_reason}以下为原方法历史归档，不代表当前行情。 ` : '';
  $('quality-note').innerHTML = `${esc(pauseNote + waiting + priceNote + difference + sourceNote)} ${usableLink}<a href="#method-details">查看口径与核验</a>`;
  setText('basis-note', d.basis_note);
  setText('footer-range', `${d.available_start} 至 ${d.available_end} · ${fmt(d.available_sessions, 0)} 个交易日`);
  $('coverage').innerHTML = [
    ['指数点位', `${fmt(d.coverage.price, 0)} 条`], [d.index.valuation.label, `${fmt(d.coverage.pe, 0)} 条`],
    ['可用隐含 EPS', `${fmt(d.coverage.implied_eps, 0)} 条`], ['正式发布日期', d.index.inception_date],
    ['最新可用 EPS', d.latest_complete_date || '暂无'],
    ['采集状态', paused ? `已暂停：${d.index.collection_disabled_reason}` : '按配置每日更新'],
    ['市场 / 计价币种', `${{ CN: '内地', HK: '香港', US: '美国' }[d.index.market]} / ${d.index.currency}（图表单位为点）`],
    ...(d.index.tracking_etfs.length ? [['对应 ETF', d.index.tracking_etfs.join(' / ')]] : []),
    ['当日 PE 对照来源', sourceNames[p.reference_pe_source] || '暂无'],
    ...(p.pe_references || []).map(r => [`${sourceNames[r.source] || r.source} PE`, `${fmt(r.pe, 3)}× · ${QUALITY[r.status]?.[0] || r.status} · ${peBasisNames[r.basis] || '供应商口径'}`]),
    ['历史统计采样', d.index.valuation.sampling_note],
    ...(proxy ? [['PE 代理标的', proxy], ['PE 快照采集时间', p.pe_observed_at || '暂无'], ['PE 日期说明', '关联 ETF 最近已收盘行情日；PE 自身生效日未提供']] : []),
    ...Object.entries(d.coverage.price_sources).map(([source, count]) => [`${sourceNames[source] || source}点位`, `${fmt(count, 0)} 条`]),
    ['数据性质', proxy ? '指数收盘点位 + 每日采集的 ETF PE 快照' : '供应商当前提供的历史序列'],
  ].map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('');
  document.querySelectorAll('[data-years]').forEach(b => b.setAttribute('aria-pressed', String(Number(b.dataset.years) === state.chartYears)));
  renderChart();
  renderBandTable();
  renderTable();
  renderPanicLine();
  renderPanicReference();
  document.dispatchEvent(new CustomEvent('index-view-changed', { detail: { key: d.index.key } }));
}

function renderPanicLine() {
  renderValuationLine('panic');
  renderValuationLine('greed');
  $('panic-live-section').hidden = false;
}

function renderValuationLine(kind) {
  const d = state.data, line = d[`${kind}_line`];
  const name = kind === 'panic' ? '恐慌' : '贪婪';
  const colorName = kind === 'panic' ? '绿色' : '红色';
  const lineText = (suffix, text) => setText(`${kind}-live-${suffix}`, text);
  const status = line?.status || 'not_calculated';
  const threshold = num(line?.threshold_pe), points = num(line?.reference_points), gap = num(line?.gap_pct);
  const years = line?.history_years || 5;
  lineText('stamp', `估值日期 ${line?.as_of || d.selected.trade_date} · ${sourceNames[line?.source] || d.index.valuation.label}`);
  lineText('status', PANIC_STATUS[status] || '暂无可用线');
  lineText('threshold', threshold == null ? '暂不设定' : `${fmt(threshold)}×`);
  lineText('threshold-note', `此前${years}年 P${fmt((num(line?.quantile) ?? (kind === 'panic' ? .1 : .9)) * 100, 0)}${line?.threshold_to_mean == null ? '' : ` · 均值的 ${percent(num(line.threshold_to_mean) * 100)}`}`);
  lineText('points', fmt(points));
  lineText('points-note', points == null ? `缺少有效隐含 EPS 或${name} PE，保留空值` : `${line.as_of} 隐含 EPS × ${fmt(threshold)}倍`);
  lineText('gap', gap == null ? '—' : gap === 0 ? `等于${name}线` : `${gap > 0 ? '高于' : '低于'} ${percent(Math.abs(gap))}`);
  const selectedPe = line?.pe ?? d.selected.pe;
  lineText('selected', `${line?.as_of || d.selected.trade_date} · PE ${selectedPe == null ? '缺失' : `${fmt(selectedPe, 3)}×`}`);
  lineText('count', num(line?.history_count) == null ? '—' : `${fmt(line.history_count, 0)} 个${line.history_frequency === 'weekly' ? '周' : '日'}观测`);
  lineText('history', line?.history_start ? `${line.history_start} 至 ${line.history_end}${line.history_complete ? '' : ' · 不足完整5年'}` : '暂无有效 PE 历史');
  const notes = {
    reference: points == null ? `PE ${name}线已计算；所选日隐含 EPS 不可用，仅在 PE 走势图绘线。` : `${colorName}水平线与上方图表一致；每日估值更新会重新计算并保存。`,
    insufficient_history: `需要跨度满5年的同口径 PE 历史及足够有效样本；历史不足时不以短样本替代${name}线。`,
    missing_pe: '所选日缺少有效正 PE，暂停绘线和差距比较，不挪用相邻日期的数据。',
    collection_paused: `该指数已暂停采集，保留旧数据查阅，不生成新的${name}线。`,
    unusable_pe: '所选日 PE 未通过该方法的可用性要求，暂停绘线和差距比较。',
    different_basis: '所选日 PE 与当前来源口径不一致，暂停绘线和差距比较。',
    different_method: '所选日估值方法与当前方法不一致，暂停绘线和差距比较。',
    not_calculated: `当前方法尚无已计算的${name}线；完成估值更新后自动生成。`,
  };
  lineText('note', notes[status] || `暂无可用${name}线。`);
  $(`${kind}-live-overview-rows`).innerHTML = (d[`${kind}_overview`] || []).map(row => {
    const item = row.line, itemStatus = item?.status || 'not_calculated';
    return `<tr${row.key === d.index.key ? ' class="panic-current" aria-current="true"' : ''}>
      <td><a href="/?index=${encodeURIComponent(row.key)}">${esc(row.name)}</a></td>
      <td>${item?.threshold_pe == null ? '—' : `${fmt(item.threshold_pe)}×`}</td><td>${fmt(item?.reference_points)}</td>
      <td>${esc(sourceNames[item?.source] || '—')}</td><td>${esc(item?.as_of || '—')}</td>
      <td>${fmt(item?.history_count, 0)}</td><td>${esc(PANIC_STATUS[itemStatus] || '暂无可用线')}</td></tr>`;
  }).join('') || '<tr><td colspan="7" class="panic-empty">尚无已计算的结果</td></tr>';
}

function renderPanicReference() {
  const research = state.data.panic_reference;
  if (!research) return;
  const r = research.record, c = research.comparison, three = r?.three_year, one = r?.one_year;
  const returnPct = value => num(value) == null ? '—' : percent(num(value) * 100);
  const peRange = three?.observed_pe_range;
  const statuses = { no_record: '尚无研究记录', insufficient_history: 'PE 历史不足5年', missing_pe: '研究时缺少 PE' };
  setText('panic-stamp', `研究日期 ${research.study_date}${r ? ` · ${sourceNames[r.source] || '无 PE 来源'} · 数据截至 ${r.cutoff}` : ''}`);
  setText('panic-status', r?.status === 'missing_pe' && c.status === 'insufficient_history' ? 'PE 历史待积累' : statuses[r?.status || 'no_record'] || (three.n ? '历史证据有限' : '暂无成熟样本'));
  setText('panic-threshold', r?.threshold_pe == null ? '暂不设定' : `≤ ${fmt(r.threshold_pe)}×`);
  setText('panic-threshold-note', r?.threshold_to_mean == null ? (statuses[r?.status || 'no_record'] || '') : `研究日前5年 P10 · 均值的 ${returnPct(r.threshold_to_mean)}`);
  const gap = num(c.gap_pct);
  setText('panic-gap', gap == null ? '—' : gap === 0 ? '等于观察线' : `${gap > 0 ? '高于' : '低于'} ${percent(Math.abs(gap))}`);
  setText('panic-selected', `${c.date} · PE ${c.pe == null ? '缺失' : `${fmt(c.pe, 3)}×`}`);
  setText('panic-observed-range', peRange ? `${fmt(peRange[0])}${peRange[0] === peRange[1] ? '' : `–${fmt(peRange[1])}`}×` : '—');
  setText('panic-sample-count', three?.n ? `${three.n} 个成熟月份 · ${three.nonoverlap.n} 个非重叠窗口` : '尚无成熟3年样本');
  setText('panic-return', returnPct(three?.median_annualized_return));
  setText('panic-baseline', r?.baseline_three_year.n ? `全部合格月份 ${returnPct(r.baseline_three_year.median_annualized_return)}（n=${r.baseline_three_year.n}）` : '尚无可比的成熟基准');
  const messages = {
    before_study: `研究记录晚于所选日期；保留记录供查阅，暂停差距比较，不用于当时判断。`,
    missing_pe: '所选日没有有效正 PE，无法计算差距。',
    unusable_pe: '所选日 PE 因来源重大差异停用，暂停差距比较。',
    different_basis: '所选日 PE 来源或口径与研究不同，暂停差距比较。',
    insufficient_history: '完整5年历史不足，保留缺口，不以短样本分位替代观察线。',
    no_record: '此指数尚未加入研究记录。',
  };
  let message = messages[c.status] || '差距 =（所选日 PE ÷ 固定观察上限 − 1）× 100%；只比较估值，不判断当日是否触发联合条件。';
  if (c.status === 'available' && c.pe_comparison_status === 'error') message += ' 所选日 PE 存在大于5%的来源差异，差距仅作主源数值对照。';
  if (c.method_changed) message += ' 现行统计规则已调整，此处保留研究时的结果。';
  setText('panic-comparison-note', message);
  setText('panic-risk', one?.n ? `随后1年价格年化中位数 ${returnPct(one.median_annualized_return)}（n=${one.n}）；1年内最差收盘浮亏 ${returnPct(one.worst_loss_from_entry)}。与3年统计的成熟样本可能不同。` : '当前规则尚无成熟1年回报样本，不能判断触发后的收益或继续下跌幅度。');
  setText('panic-alternatives', r?.reference_thresholds.length ? `独立来源观察线：${r.reference_thresholds.map(a => `${sourceNames[a.source] || a.source} ≤ ${fmt(a.threshold_pe)}×（${a.cutoff}）`).join('；')}。各自同源历史计算，口径不可互换。` : '暂无独立来源的完整5年 PE 观察线可对照。');
  $('panic-notes').innerHTML = (r?.notes || []).map(note => `<li>${esc(note)}</li>`).join('');
  setText('panic-window-count', `${three?.nonoverlap.n || 0} 个非重叠窗口`);
  $('panic-samples').innerHTML = (three?.nonoverlap_samples || []).map(s => `<tr>
    <td>${esc(s.signal_date)}<small class="panic-holding">观察期 ${esc(s.entry_date)} 至 ${esc(s.exit_date)}</small></td>
    <td>${fmt(s.signal_pe)}×</td><td>${percent(s.percentile)}</td><td>${returnPct(s.signal_drawdown)}</td>
    <td>${returnPct(s.annualized_return)}</td><td>${returnPct(s.lowest_return_from_entry)}</td>
    <td><span class="quality ${esc(QUALITY[s.pe_comparison_status]?.[1] || 'missing')}">${esc(QUALITY[s.pe_comparison_status]?.[0] || '暂无对照')}</span></td>
  </tr>`).join('') || '<tr><td colspan="7" class="panic-empty">尚无成熟的非重叠3年窗口</td></tr>';
  setText('panic-rule', `历史联合条件：${research.rule}`);
  setText('panic-history', r?.history_start ? `观察线历史区间：${r.history_start} 至 ${r.history_end}，${fmt(r.history_count, 0)} 个有效观测${r.source === 'danjuan' ? '（已结束自然周采样）' : '（日度采样）'}${r.history_complete ? '。' : '；不足完整5年，未设定观察线。'}` : '研究时没有可用于观察线的 PE 历史。');
  $('panic-overview-rows').innerHTML = research.overview.map(row => `<tr${row.key === state.data.index.key ? ' class="panic-current" aria-current="true"' : ''}>
    <td><a href="/?index=${encodeURIComponent(row.key)}">${esc(row.name)}</a></td>
    <td>${row.threshold_pe == null ? esc(statuses[row.status] || '暂不设定') : `≤ ${fmt(row.threshold_pe)}×`}</td>
    <td>${esc(sourceNames[row.source] || '—')}</td><td>${esc(row.cutoff || '—')}</td>
    <td>${fmt(row.months, 0)}</td><td>${fmt(row.nonoverlap_windows, 0)}</td></tr>`).join('');
  $('panic-section').hidden = false;
}

function renderPriceReturn() {
  const r = state.data.price_return;
  const value = num(r.annualized_return_pct);
  setText('annualized-return', value == null ? '—' : `${value > 0 ? '+' : ''}${fmt(value, 2)}%`);
  setText('return-basis', `${state.chartYears ? `近${state.chartYears}年` : '全部历史'} · 不含分红`);
  setText('return-range', r.start_date ? `${r.start_date} 至 ${r.end_date}` : '区间内暂无价格');
  const notes = [];
  if (r.status === 'insufficient_data') notes.push('至少需要两个不同日期的价格');
  if (r.status === 'invalid_price') notes.push('端点价格无效');
  if (r.status === 'price_discrepancy') notes.push('端点价格存在重大差异');
  if (r.status === 'ok' && r.price_status === 'error') notes.push('端点价格存在差异，采用指数官方数据');
  if (r.status === 'ok' && r.elapsed_days < 365) notes.push('不足1年，按实际天数折算');
  if (r.price_status === 'unverified') notes.push('端点含未核验价格');
  if (r.price_status === 'warning') notes.push('端点价格存在1–5%差异');
  if (r.includes_backcast) notes.push('含发布前回溯数据');
  if (r.end_date && r.end_date < state.data.selected.trade_date) notes.push('截至最后可用价格日');
  setText('return-status', notes.join(' · '));
  $('return-status').hidden = notes.length === 0;
  $('annualized-return').title = r.start_price && r.end_price
    ? `${sourceNames[r.start_source] || r.start_source} ${fmt(r.start_price)} → ${sourceNames[r.end_source] || r.end_source} ${fmt(r.end_price)}；相隔 ${r.elapsed_days} 天`
    : '';
}

function bandDefinitions() {
  if (state.mode === 'fixed') {
    const keys = state.data.index.band_multiples.map(String).sort((a, b) => Number(a) - Number(b));
    const hasEight = keys.includes('8');
    return keys.map((key, i) => ({ key, label: `${key}×`, color: key === '8' ? '#196657' : colors[(i - (hasEight && Number(key) > 8 ? 1 : 0)) % colors.length], field: 'fixed' }));
  }
  return ['0.1', '0.25', '0.5', '0.75', '0.9'].map((key, i) => ({ key, label: `${Number(key) * 100}%`, color: colors[i], field: state.mode === 'pe' ? 'quantile_pe' : 'quantile_points' }));
}

function meanDefinition() {
  return { label: `${windowName()}平均 PE`, color: meanColor, field: state.mode === 'pe' ? 'mean_pe' : 'mean_points', mean: true };
}

function valuationLineDefinition(kind) {
  const line = state.data[`${kind}_line`];
  if (line?.status !== 'reference' || num(line.pe) <= 0 || state.data.selected.pe_usable === false) return null;
  const name = kind === 'panic' ? '恐慌' : '贪婪';
  const quantile = kind === 'panic' ? 'P10' : 'P90';
  const pe = num(line.threshold_pe);
  const value = state.mode === 'pe' ? pe : num(line.reference_points);
  if (pe == null || pe <= 0 || value == null || value <= 0) return null;
  return {
    kind, name, color: kind === 'panic' ? panicColor : greedColor,
    value,
    label: `${name} ${fmt(pe)}×${state.mode === 'pe' ? '' : ` · ${fmt(value)}点`}`,
    note: state.mode === 'pe'
      ? `${name}水平线为 ${line.as_of} 之前近5年有效 PE 的 ${quantile}：${fmt(pe)}×，随每日估值更新重算。`
      : `${name}水平线 = ${line.as_of} 隐含 EPS × ${fmt(pe)}倍，固定为 ${fmt(value)}点；PE 取此前近5年 ${quantile}，随每日估值更新重算。`,
  };
}

function renderBandTable() {
  const p = state.data.selected, definitions = [...bandDefinitions(), meanDefinition()];
  setText('band-date', p.trade_date);
  setText('band-heading', state.mode === 'fixed' ? '倍数对应点位' : '分位对应点位');
  setText('band-first-column', state.mode === 'fixed' ? 'PE' : '历史分位');
  $('band-rows').innerHTML = definitions.map(def => {
    const value = def.mean ? p.mean_points : state.mode === 'fixed' ? p.fixed[def.key] : p.quantile_points[def.key];
    const delta = num(value) != null && num(p.price) > 0 ? (num(value) / num(p.price) - 1) * 100 : null;
    const label = def.mean ? `平均 ${p.mean_pe == null ? '—' : `${fmt(p.mean_pe, 2)}×`}` : state.mode === 'fixed' ? def.label : `${def.label} / ${fmt(p.quantile_pe[def.key], 1)}×`;
    return `<tr${def.mean ? ' class="mean-row"' : ''}><td><span class="band-key"><i style="--c:${def.color}"></i>${esc(label)}</span></td><td>${fmt(value)}</td><td class="delta ${delta < 0 ? 'negative' : 'positive'}">${delta > 0 ? '+' : ''}${percent(delta)}</td></tr>`;
  }).join('') || '<tr><td colspan="3">该区间暂无可用的点位估值带</td></tr>';
  setText('band-equation', `${fmt(p.implied_eps)} × ${state.mode === 'fixed' ? 'PE 倍数' : '历史分位 PE'}`);
  setText('band-footnote', `平均 PE 取${windowName()}窗口内、估值日前 ${fmt(p.history_count, 0)} 个有效正 PE 的算术平均；至少需 ${state.data.minimum_history} 个观测。${state.data.index.valuation.sampling_note}${p.history_start ? `实际样本：${p.history_start} 至 ${p.history_end}。` : ''}${p.history_span_short ? `现有 PE 历史不足${state.data.history_years}年。` : ''}参考点位 = 当日隐含 EPS × 对应 PE。`);
}

let plot = null;
function renderChart() {
  if (!state.data) return;
  const sampled = state.data.index.valuation.history_frequency === 'weekly' && state.data.series.some(row => row.pe != null);
  const rows = sampled ? state.data.series.filter((row, i, all) => row.pe != null || i === all.length - 1) : state.data.series;
  const bands = bandDefinitions(), definitions = [...bands, meanDefinition()];
  const svg = $('chart'), width = Math.max(280, svg.clientWidth || 1000);
  const height = width < 540 ? 290 : Math.max(300, Math.min(440, width * .43));
  const margin = { left: width < 540 ? 46 : 63, right: width < 540 ? 9 : 21, top: 24, bottom: 43 };
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const actualKey = state.mode === 'pe' ? 'pe' : 'price';
  const actualLabel = state.mode === 'pe' ? (state.data.index.valuation.proxy_symbol ? 'ETF 代理 PE' : '指数 PE') : '收盘点位';
  const valueFor = (row, def) => num(def.mean ? row[def.field] : row[def.field]?.[def.key]);
  const values = rows.flatMap(row => [num(row[actualKey]), ...definitions.map(def => valueFor(row, def))]).filter(v => v != null);
  const referenceLines = ['panic', 'greed'].map(valuationLineDefinition).filter(Boolean);
  values.push(...referenceLines.map(line => line.value));
  $('tooltip').hidden = true;
  setText('chart-title', { fixed: 'PE 估值带', quantile: '历史分位估值带', pe: 'PE 历史走势' }[state.mode] + (sampled ? ' · 按 PE 实际采样日' : ''));
  document.querySelectorAll('[data-mode]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === state.mode)));
  if (!values.length) {
    svg.innerHTML = `<text x="${width / 2}" y="${height / 2}" text-anchor="middle">当前区间没有可展示的数据。</text>`;
    plot = null;
    return;
  }
  const lo = Math.min(...values), hi = Math.max(...values), spread = hi - lo || Math.abs(hi) * .1 || 1;
  const yMin = state.mode === 'pe' ? lo - spread * .09 : Math.max(0, lo - spread * .09), yMax = hi + spread * .12;
  const x = i => margin.left + i / Math.max(1, rows.length - 1) * (width - margin.left - margin.right);
  const y = value => margin.top + (yMax - value) / (yMax - yMin) * (height - margin.top - margin.bottom);
  const line = get => {
    let path = '', drawing = false;
    rows.forEach((row, i) => { const v = get(row); if (v == null) { drawing = false; return; } path += `${drawing ? 'L' : 'M'}${x(i).toFixed(2)},${y(v).toFixed(2)}`; drawing = true; });
    return path;
  };
  const area = (lower, upper) => {
    const segments = []; let segment = [];
    rows.forEach((row, i) => { const a = valueFor(row, lower), b = valueFor(row, upper); if (a == null || b == null) { if (segment.length) segments.push(segment); segment = []; } else segment.push([i, a, b]); });
    if (segment.length) segments.push(segment);
    return segments.map(s => `M${s.map(([i, a]) => `${x(i).toFixed(2)},${y(a).toFixed(2)}`).join('L')}L${s.slice().reverse().map(([i, , b]) => `${x(i).toFixed(2)},${y(b).toFixed(2)}`).join('L')}Z`).join('');
  };
  let content = `<title>${esc(state.data.index.name)}${esc($('chart-title').textContent)}</title><desc>左右方向键逐日查看，回车选择估值日期。缺失记录不连线。</desc>`;
  for (let i = 0; i <= 5; i++) {
    const value = yMin + (yMax - yMin) / 5 * i, py = y(value);
    content += `<line x1="${margin.left}" y1="${py}" x2="${width - margin.right}" y2="${py}" stroke="#edf1f6"/><text x="${margin.left - 12}" y="${py + 4}" text-anchor="end">${fmt(value, state.mode === 'pe' ? 1 : 0)}</text>`;
  }
  const tickCount = Math.min(width < 540 ? 4 : 6, rows.length);
  const tickIndexes = [...new Set(Array.from({ length: tickCount }, (_, i) => Math.round((rows.length - 1) * i / Math.max(1, tickCount - 1))))];
  tickIndexes.forEach(i => { content += `<text x="${x(i)}" y="${height - 13}" text-anchor="${i === 0 ? 'start' : i === rows.length - 1 ? 'end' : 'middle'}">${esc(rows[i].trade_date.slice(0, 7))}</text>`; });
  for (let i = 0; i < bands.length - 1; i++) content += `<path d="${area(bands[i], bands[i + 1])}" fill="${bands[i].color}" fill-opacity=".065"/>`;
  definitions.forEach(def => {
    content += `<path${def.mean ? ' id="mean-line" stroke-dasharray="6 4"' : ''} d="${line(row => valueFor(row, def))}" fill="none" stroke="${def.color}" stroke-width="${def.mean ? 2 : 1.2}" stroke-opacity=".85" vector-effect="non-scaling-stroke"/>`;
    rows.forEach((row, i) => {
      const value = valueFor(row, def);
      if (value != null && (i === 0 || valueFor(rows[i - 1], def) == null) && (i === rows.length - 1 || valueFor(rows[i + 1], def) == null)) content += `<circle class="band-observation" cx="${x(i)}" cy="${y(value)}" r="3" fill="${def.color}"/>`;
    });
  });
  content += `<path d="${line(row => num(row[actualKey]))}" fill="none" stroke="#203d88" stroke-width="2.3" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>`;
  const last = rows[rows.length - 1];
  if (num(last[actualKey]) != null) content += `<circle cx="${x(rows.length - 1)}" cy="${y(num(last[actualKey]))}" r="4" fill="#203d88" stroke="white" stroke-width="2"/>`;
  referenceLines.forEach(reference => {
    const py = y(reference.value);
    content += `<g><title>${esc(reference.note)}</title><line id="${reference.kind}-line" data-value="${reference.value}" x1="${margin.left}" x2="${width - margin.right}" y1="${py}" y2="${py}" stroke="${reference.color}" stroke-width="2" vector-effect="non-scaling-stroke"/><text id="${reference.kind}-line-label" x="${width - margin.right - 5}" y="${Math.max(margin.top + 15, py - 8)}" text-anchor="end" style="fill:${reference.color}">${esc(reference.label)}</text></g>`;
  });
  content += `<g id="chart-hover" visibility="hidden"><line id="hover-line" y1="${margin.top}" y2="${height - margin.bottom}" stroke="#97a6be" stroke-dasharray="4 4"/><circle id="hover-dot" r="4" fill="#203d88" stroke="white" stroke-width="2"/></g>`;
  svg.innerHTML = content;
  positionReferenceLabels(svg, referenceLines, y, margin, width, height);
  plot = { rows, x, y, width, height, margin, actualKey };
  $('legend').innerHTML = `<span><i style="--c:#203d88"></i>${actualLabel}</span>` + definitions.map(def => `<span><i${def.mean ? ' class="mean-key"' : ''} style="--c:${def.color}"></i>${esc(def.label)}</span>`).join('') + referenceLines.map(reference => `<span><i style="--c:${reference.color}"></i>${reference.name}估值线</span>`).join('');
  const lineNotes = ['panic', 'greed'].map(kind => referenceLines.find(reference => reference.kind === kind)?.note || (state.data[`${kind}_line`]?.status === 'reference' ? `所选日缺少可用隐含 EPS，${kind === 'panic' ? '恐慌' : '贪婪'}点位线暂不绘制。` : `${kind === 'panic' ? '恐慌' : '贪婪'}线暂不可用，原因见下方滚动估值线。`));
  setText('chart-caption', `${state.mode === 'fixed' ? '固定倍数线 = 当日隐含 EPS × 固定 PE；虚线 = 当日隐含 EPS × 历史平均 PE。' : state.mode === 'pe' ? '虚线为历史平均 PE。' : '虚线 = 当日隐含 EPS × 历史平均 PE。'} 均值与分位按${windowName()}滚动计算，仅用此前数据；不足 ${state.data.minimum_history} 个观测时留空。${state.data.selected.history_span_short ? ` 当前可用 PE 历史不足${state.data.history_years}年，按实际样本计算。` : ''} ${lineNotes.join(' ')}`);
}

function positionReferenceLabels(svg, references, y, margin, width, height) {
  const labels = references.map(reference => ({
    node: $(`${reference.kind}-line-label`), lineY: y(reference.value),
  })).sort((a, b) => a.lineY - b.lineY);
  let previousBottom = margin.top - 4;
  labels.forEach(({ node, lineY }) => {
    const availableWidth = width - margin.left - margin.right - 10;
    const naturalWidth = node.getComputedTextLength();
    if (naturalWidth > availableWidth) {
      node.setAttribute('textLength', availableWidth);
      node.setAttribute('lengthAdjust', 'spacingAndGlyphs');
    }
    const box = node.getBBox();
    const baseline = Number(node.getAttribute('y'));
    const aboveBaseline = baseline - box.y;
    const belowBaseline = box.height - aboveBaseline;
    const desired = Math.max(margin.top + aboveBaseline + 2, lineY - 8, previousBottom + aboveBaseline + 5);
    node.setAttribute('y', Math.min(height - margin.bottom - belowBaseline - 2, desired));
    previousBottom = Number(node.getAttribute('y')) + belowBaseline;
  });
}

function showHover(index) {
  if (!plot) return;
  state.hover = Math.max(0, Math.min(plot.rows.length - 1, index));
  const row = plot.rows[state.hover], px = plot.x(state.hover), value = num(row[plot.actualKey]);
  $('chart-hover').setAttribute('visibility', 'visible');
  $('hover-line').setAttribute('x1', px); $('hover-line').setAttribute('x2', px);
  $('hover-dot').setAttribute('cx', px); $('hover-dot').setAttribute('cy', value == null ? 0 : plot.y(value));
  $('hover-dot').setAttribute('visibility', value == null ? 'hidden' : 'visible');
  const tip = $('tooltip');
  tip.innerHTML = `<strong>${esc(row.trade_date)}${row.phase === 'backcast' ? ' · 回溯' : ''}</strong><div><span>收盘点位</span>${fmt(row.price)}</div><div><span>指数 PE</span>${fmt(row.pe, 3)}</div><div><span>${windowName()}平均 PE</span>${fmt(row.mean_pe, 2)}</div><div><span>隐含 EPS</span>${fmt(row.implied_eps)}</div><div><span>${windowName()}分位</span>${percent(row.percentile)}</div>`;
  tip.hidden = false;
  const box = $('chart-wrap').getBoundingClientRect();
  const point = new DOMPoint(px, 80).matrixTransform($('chart').getScreenCTM());
  const left = point.x - box.left;
  tip.style.left = `${Math.max(0, Math.min(box.width - tip.offsetWidth, left > box.width / 2 ? left - tip.offsetWidth - 14 : left + 14))}px`;
  tip.style.top = '30px';
}

function renderTable() {
  const rows = state.data.series.slice().reverse(), size = 12;
  const offset = state.page * size, page = rows.slice(offset, offset + size);
  $('history-rows').innerHTML = page.map(p => {
    const q = QUALITY[p.pe_comparison_status] || QUALITY.missing;
    return `<tr><td><button class="date-button" data-date="${esc(p.trade_date)}">${esc(p.trade_date)}</button>${p.phase === 'backcast' ? '<span class="phase-label">回溯</span>' : ''}</td><td>${fmt(p.price)}</td><td>${fmt(p.pe, 3)}</td><td>${fmt(p.mean_pe, 2)}</td><td>${fmt(p.implied_eps)}</td><td>${fmt(p.pb, 3)}</td><td>${fmt(p.ps, 3)}</td><td>${fmt(p.reference_pe, 2)}</td><td><span class="quality ${q[1]}">${q[0]}</span></td><td>${esc(sourceNames[p.price_source] || '—')}${p.price_status === 'unverified' ? '（待核验）' : p.price_status === 'error' ? '（有差异）' : ''}</td></tr>`;
  }).join('');
  setText('page-info', `${rows.length ? offset + 1 : 0}–${Math.min(rows.length, offset + size)} / ${fmt(rows.length, 0)} 条`);
  $('previous-page').disabled = state.page === 0;
  $('next-page').disabled = offset + size >= rows.length;
}

document.querySelectorAll('[data-years]').forEach(button => button.addEventListener('click', () => { state.chartYears = Number(button.dataset.years); load(); }));
document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => { state.mode = button.dataset.mode; renderChart(); if (state.data) renderBandTable(); }));
$('as-of').addEventListener('change', () => { if (!$('as-of').value || !$('as-of').checkValidity()) return; state.asOf = $('as-of').value; load(); });
$('latest').addEventListener('click', () => { state.asOf = null; load(); });
$('history-window').addEventListener('change', () => { state.historyYears = Number($('history-window').value); load(); });
$('index-select').addEventListener('change', () => { state.index = $('index-select').value; state.asOf = null; rememberIndex(); load(); });
$('retry').addEventListener('click', load);
$('quality-note').addEventListener('click', event => { if (event.target.closest('a')) $('method-details').open = true; });
let resizeFrame;
window.addEventListener('resize', () => { cancelAnimationFrame(resizeFrame); resizeFrame = requestAnimationFrame(() => renderChart()); });
$('previous-page').addEventListener('click', () => { if (state.page > 0) { state.page--; renderTable(); } });
$('next-page').addEventListener('click', () => { if ((state.page + 1) * 12 < state.data.series.length) { state.page++; renderTable(); } });
$('history-rows').addEventListener('click', event => { const button = event.target.closest('[data-date]'); if (button) { state.asOf = button.dataset.date; load(); window.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' }); } });
$('quality-note').addEventListener('click', event => { if (event.target.id === 'last-usable') { state.asOf = state.data.latest_complete_date; load(); } });
$('chart').addEventListener('pointermove', event => {
  if (!plot) return;
  const p = new DOMPoint(event.clientX, event.clientY).matrixTransform($('chart').getScreenCTM().inverse());
  showHover(Math.round((p.x - plot.margin.left) / (plot.width - plot.margin.left - plot.margin.right) * (plot.rows.length - 1)));
});
$('chart').addEventListener('pointerleave', () => { $('tooltip').hidden = true; $('chart-hover')?.setAttribute('visibility', 'hidden'); });
$('chart').addEventListener('keydown', event => {
  if (!plot || !['ArrowLeft', 'ArrowRight', 'Enter'].includes(event.key)) return;
  event.preventDefault();
  if (event.key === 'Enter' && state.hover >= 0) { state.asOf = plot.rows[state.hover].trade_date; load(); }
  else showHover(state.hover < 0 ? plot.rows.length - 1 : state.hover + (event.key === 'ArrowLeft' ? -1 : 1));
});

async function start() {
  try {
    const response = await fetch('/api/indices');
    if (!response.ok) throw new Error('无法获取指数列表，请检查本地服务。');
    const indices = await response.json();
    $('index-select').innerHTML = indices.map(index => `<option value="${esc(index.key)}">${esc(index.name)}${index.tracking_etfs.length ? `（${esc(index.tracking_etfs.join('/'))}）` : ''}</option>`).join('');
    state.index = indices.some(index => index.key === state.index) ? state.index : indices[0].key;
    $('index-select').value = state.index;
    rememberIndex();
    await load();
  } catch (error) {
    setText('error-text', error.message); $('error').hidden = false; $('loading').hidden = true;
  }
}
start();
