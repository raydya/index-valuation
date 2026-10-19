(() => {
  'use strict';

  const section = document.getElementById('market-overview');
  if (!section) return;
  const groups = document.getElementById('market-overview-groups');
  const refresh = document.getElementById('market-overview-refresh');
  const errorBox = document.getElementById('market-overview-error');
  const stamp = document.getElementById('market-overview-stamp');
  const counts = document.getElementById('market-overview-counts');
  const signals = {
    panic: { label: '恐慌', action: '持续买入', light: '绿灯' },
    normal: { label: '普通', action: '继续持有', light: '黄灯' },
    greed: { label: '贪婪', action: '减仓离场', light: '红灯' },
    unknown: { label: '暂无判断', action: '', light: '灰灯' },
  };
  const markets = { CN: '内地', HK: '香港', US: '美国' };
  const timeFormat = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  });
  let lastSnapshot = null;
  let fetching = false;
  let lastAttempt = 0;
  let selectedIndex = new URLSearchParams(location.search).get('index');

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function pe(value) {
    if (value == null || value === '' || !Number.isFinite(Number(value))) return '未记录';
    return `${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}×`;
  }

  function date(value) {
    return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : '日期未记录';
  }

  function markSelected() {
    groups.querySelectorAll('[data-market-index]').forEach(link => {
      const selected = link.dataset.marketIndex === selectedIndex;
      link.classList.toggle('market-item-selected', selected);
      if (selected) link.setAttribute('aria-current', 'true');
      else link.removeAttribute('aria-current');
    });
  }

  function renderItem(item) {
    const signal = Object.hasOwn(signals, item.signal) ? item.signal : 'unknown';
    const state = signals[signal];
    const row = element('li', `market-item market-item-${signal}`);
    const link = element('a', 'market-index-link');
    link.href = `/?index=${encodeURIComponent(item.key)}`;
    link.dataset.marketIndex = String(item.key);
    link.dataset.signal = signal;
    const heading = element('span', 'market-item-heading');
    heading.append(element('strong', 'market-index-name', item.name));
    const judgement = element('span', `market-judgement market-judgement-${signal}`);
    const dot = element('span', `market-dot market-dot-${signal}`);
    dot.setAttribute('aria-hidden', 'true');
    judgement.append(dot, element('span', 'visually-hidden', `${state.light}：`),
      element('span', 'market-action', `${state.label}${state.action ? `，${state.action}` : ''}`));
    heading.append(judgement);
    const values = element('span', 'market-item-values');
    values.append(element('span', 'market-index-pe', `PE ${pe(item.pe)}`),
      element('span', 'market-index-date', date(item.as_of)));
    link.append(heading, values);
    if (signal === 'unknown') link.append(element('span', 'market-reason', item.reason || '当前数据或估值线不足，等待核实。'));
    const detail = [item.source_label, item.basis_note,
      `恐慌线 ${pe(item.lower_pe)}；贪婪线 ${pe(item.upper_pe)}`,
      item.history_start && item.history_end ? `样本 ${item.history_start} 至 ${item.history_end}，${item.history_count ?? '未知'} 个观测` : '样本范围未记录',
      item.reason].filter(Boolean).join('；');
    link.title = detail;
    row.append(link);
    return row;
  }

  function render(snapshot) {
    const totals = { panic: 0, normal: 0, greed: 0, unknown: 0 };
    snapshot.items.forEach(item => { totals[Object.hasOwn(signals, item.signal) ? item.signal : 'unknown']++; });
    counts.textContent = `共 ${snapshot.items.length} 个指数：恐慌 ${totals.panic}，普通 ${totals.normal}，贪婪 ${totals.greed}，暂无判断 ${totals.unknown}`;
    const checked = new Date(snapshot.checked_at);
    stamp.textContent = Number.isNaN(checked.getTime()) ? '读取时间待核实' : `读取于 ${timeFormat.format(checked)} 北京时间`;
    if (snapshot.rule?.description) document.getElementById('market-overview-rule-text').textContent = snapshot.rule.description;
    if (JSON.stringify(snapshot.items) !== JSON.stringify(lastSnapshot?.items)) {
      const focusedKey = document.activeElement?.closest('[data-market-index]')?.dataset.marketIndex;
      const fragment = document.createDocumentFragment();
      const marketCodes = [...new Set(['CN', 'HK', 'US', ...snapshot.items.map(item => item.market)])];
      marketCodes.forEach(market => {
        const items = snapshot.items.filter(item => item.market === market);
        if (!items.length) return;
        const group = element('section', 'market-group');
        group.dataset.market = String(market);
        const heading = element('h3', '', markets[market] || '其他');
        const list = element('ul', 'market-items');
        items.forEach(item => list.append(renderItem(item)));
        group.append(heading, list);
        fragment.append(group);
      });
      if (!snapshot.items.length) fragment.append(element('p', 'market-overview-empty', '尚无可展示的指数记录。'));
      groups.replaceChildren(fragment);
      if (focusedKey) [...groups.querySelectorAll('[data-market-index]')]
        .find(link => link.dataset.marketIndex === focusedKey)?.focus({ preventScroll: true });
    }
    markSelected();
  }

  async function loadOverview() {
    if (fetching) return;
    fetching = true;
    lastAttempt = Date.now();
    refresh.disabled = true;
    refresh.textContent = '正在读取…';
    section.setAttribute('aria-busy', 'true');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch('/api/market-overview', { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error('Overview unavailable');
      const snapshot = await response.json();
      if (!snapshot || !Array.isArray(snapshot.items)) throw new Error('Invalid overview');
      render(snapshot);
      lastSnapshot = snapshot;
      errorBox.hidden = true;
      section.classList.remove('market-overview-stale');
    } catch {
      errorBox.textContent = lastSnapshot
        ? '现状刷新失败，以下为上次读取的记录，可能已过期。可点击“重试读取”。'
        : '暂时无法读取指数现状，可点击“重试读取”。下方指数估值与任务状态仍可使用。';
      errorBox.hidden = false;
      section.classList.add('market-overview-stale');
      if (!lastSnapshot) {
        counts.textContent = '指数现状暂不可用';
        groups.replaceChildren(element('p', 'market-overview-empty', '等待读取各指数的最新记录。'));
      }
    } finally {
      clearTimeout(timeout);
      fetching = false;
      refresh.disabled = false;
      refresh.textContent = errorBox.hidden ? '刷新现状' : '重试读取';
      section.setAttribute('aria-busy', 'false');
    }
  }

  groups.addEventListener('click', event => {
    const link = event.target.closest('[data-market-index]');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
    const select = document.getElementById('index-select');
    if (![...select.options].some(option => option.value === link.dataset.marketIndex)) return;
    event.preventDefault();
    select.value = link.dataset.marketIndex;
    select.dispatchEvent(new Event('change'));
    selectedIndex = link.dataset.marketIndex;
    markSelected();
    document.querySelector('.overview').scrollIntoView({
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start',
    });
  });
  document.addEventListener('index-view-changed', event => {
    selectedIndex = event.detail.key;
    markSelected();
  });
  refresh.addEventListener('click', loadOverview);
  setInterval(() => { if (!document.hidden) loadOverview(); }, 60000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Date.now() - lastAttempt >= 60000) loadOverview();
  });
  loadOverview();
})();
