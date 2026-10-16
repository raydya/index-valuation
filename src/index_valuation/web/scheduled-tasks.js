(() => {
  'use strict';

  const section = document.getElementById('scheduled-tasks');
  if (!section) return;
  const list = document.getElementById('tasks-list');
  const refresh = document.getElementById('tasks-refresh');
  const errorBox = document.getElementById('tasks-error');
  const warningsBox = document.getElementById('tasks-warnings');
  const overview = document.getElementById('tasks-overview');
  const checkedAt = document.getElementById('tasks-checked-at');
  const runStates = {
    never_run: ['尚无执行记录', 'neutral'],
    queued: ['已排队', 'pending'],
    running: ['执行中', 'pending'],
    completed: ['已完成', 'success'],
    finished: ['已返回结果', 'neutral'],
    partial: ['部分完成', 'warning'],
    failed: ['执行失败', 'error'],
    timed_out: ['执行超时', 'error'],
    unknown: ['结果待核实', 'neutral'],
  };
  const schedulerStates = {
    enabled: ['已启用', 'success'],
    running: ['已启用 · 执行中', 'pending'],
    disabled: ['已停用', 'neutral'],
    not_installed: ['未安装', 'warning'],
    unknown: ['调度待核实', 'neutral'],
  };
  const dataStates = {
    completed: '已完成', complete: '完整', collected: '已采集', available: '有可用数据', paused: '暂停采集',
    partial: '部分完成', blocked: '已暂停 / 受阻', disabled: '已暂停',
    failed: '失败', missing: '缺失', pending: '待更新',
    unverified: '待核验', unconfirmed: '口径待核实', unknown: '待核实',
    warning: '需关注', error: '存在差异', passed: '核验通过',
    finished: '已返回结果', never_run: '尚无记录',
  };
  const dateTimeFormat = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  });
  let lastSnapshot = null;
  let fetching = false;
  let lastAttempt = 0;

  // API strings are always text nodes, including the agent's original result.
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function dateTime(value) {
    if (!value) return '未记录';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? '时间待核实' : dateTimeFormat.format(parsed);
  }

  function dataDate(value) {
    return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : '未记录';
  }

  function issueText(issue) {
    if (typeof issue === 'string') return issue;
    if (!issue || typeof issue !== 'object') return '';
    return [issue.name, issue.message || issue.summary || issue.note].filter(Boolean).join('：');
  }

  function issuesFor(task) {
    return [...new Set([...asArray(task.last_run?.issues), ...asArray(task.warnings)]
      .map(issueText).filter(Boolean))];
  }

  function badge(state, states) {
    const [label, tone] = states[state] || states.unknown;
    return element('span', `task-badge task-badge-${tone}`, label);
  }

  function labelledCell(label, className) {
    const cell = element('span', `task-cell ${className}`);
    cell.append(element('span', 'task-mobile-label', label));
    return cell;
  }

  function addTimelineItem(timeline, label, value) {
    const item = element('div');
    item.append(element('dt', '', label), element('dd', '', value));
    timeline.append(item);
  }

  function renderDataItems(items) {
    const block = element('div', 'task-data');
    block.append(element('h3', '', '数据覆盖'));
    block.append(element('p', 'task-detail-help', '数据日期为最新输入；核验 / 完整截至日：指数表示具备 EPS 计算条件的日期，表格项表示已核验输入日期。未知日期保持空缺，未记录不表示没有缺口。'));
    const scroll = element('div', 'task-data-scroll');
    scroll.tabIndex = 0;
    scroll.setAttribute('role', 'region');
    scroll.setAttribute('aria-label', '任务数据覆盖，可横向滚动');
    const table = element('table', 'task-data-table');
    const head = element('thead');
    const headings = element('tr');
    ['对象', '数据日期', '核验 / 完整截至', '状态 / 说明'].forEach(text => {
      const th = element('th', '', text);
      th.scope = 'col';
      headings.append(th);
    });
    head.append(headings);
    const body = element('tbody');
    items.forEach((item, index) => {
      const row = element('tr');
      const description = element('td');
      description.append(element('span', '', dataStates[item.status] || item.status || '待核实'));
      if (item.note) {
        const note = element('details', 'task-data-note');
        note.dataset.noteIndex = String(index);
        note.append(element('summary', '', '查看说明'), element('small', '', item.note));
        description.append(note);
      }
      row.append(element('td', '', item.name || '未命名'), element('td', '', dataDate(item.date)),
        element('td', '', dataDate(item.complete_date)), description);
      body.append(row);
    });
    table.append(head, body);
    scroll.append(table);
    block.append(scroll);
    return block;
  }

  function renderTask(task, index, open) {
    const run = task.last_run || {};
    const scheduler = task.scheduler || {};
    const status = run.status || 'never_run';
    const issues = issuesFor(task);
    const row = element('details', 'task-row');
    row.dataset.taskId = String(task.id || index);
    row.open = open;
    const summary = element('summary', 'task-summary');
    const identity = element('span', 'task-identity');
    identity.append(element('strong', 'task-name', task.name || '未命名任务'));
    identity.append(element('span', 'task-excerpt', run.summary || (status === 'never_run' ? '等待首次执行记录' : '展开查看执行详情')));
    const plan = labelledCell('每日计划 / 调度', 'task-plan');
    plan.append(element('span', 'task-plan-time', task.schedule_text || '计划待核实'), badge(scheduler.state, schedulerStates));
    const execution = labelledCell('最近执行', 'task-execution');
    execution.append(badge(status, runStates));
    const timestamp = status === 'queued' ? run.queued_at : run.finished_at || run.started_at || run.queued_at;
    if (timestamp) execution.append(element('span', 'task-time', dateTime(timestamp)));
    const next = labelledCell('下次计划', 'task-next');
    next.append(element('span', 'task-time', task.next_run_at ? dateTime(task.next_run_at) : '未安排 / 待核实'));
    if (issues.length) next.append(element('span', 'task-issue-count', `${issues.length} 项需关注`));
    const marker = element('span', 'task-chevron', '⌄');
    marker.setAttribute('aria-hidden', 'true');
    summary.append(identity, plan, execution, next, marker);
    row.append(summary);

    const detail = element('div', 'task-detail');
    if (task.description) detail.append(element('p', 'task-description', task.description));
    const timeline = element('dl', 'task-timeline');
    addTimelineItem(timeline, '排队时间', dateTime(run.queued_at));
    addTimelineItem(timeline, '开始时间', dateTime(run.started_at));
    addTimelineItem(timeline, '结束时间', dateTime(run.finished_at));
    addTimelineItem(timeline, '执行退出码', run.exit_code == null ? '未记录' : String(run.exit_code));
    detail.append(timeline);
    if (status === 'queued') detail.append(element('p', 'task-detail-help', '任务已进入队列，尚未确认开始执行或更新数据。'));
    if (status === 'finished') detail.append(element('p', 'task-detail-help', '已收到执行结果。具体更新范围与缺口以以下记录为准，不能据此认定全部数据已更新。'));
    if (run.summary) detail.append(element('p', 'task-full-summary', run.summary));
    if (issues.length) {
      const issueBlock = element('div', 'task-issues');
      issueBlock.append(element('h3', '', '需关注'));
      const issueList = element('ul');
      issues.forEach(issue => issueList.append(element('li', '', issue)));
      issueBlock.append(issueList);
      detail.append(issueBlock);
    }
    const items = asArray(task.data_items);
    if (items.length) detail.append(renderDataItems(items));
    if (run.result_text) {
      const report = element('details', 'task-report');
      report.append(element('summary', '', '查看执行结果原文'));
      const resultText = element('pre', 'task-result-text', run.result_text);
      resultText.tabIndex = 0;
      resultText.setAttribute('role', 'region');
      resultText.setAttribute('aria-label', '执行结果原文，可滚动阅读');
      report.append(resultText);
      detail.append(report);
    } else if (!items.length && !run.summary && !issues.length) {
      detail.append(element('p', 'task-detail-help', '尚无可展示的执行结果或数据日期。'));
    }
    const schedulerMeta = [scheduler.label ? `调度标识：${scheduler.label}` : '',
      Number.isInteger(scheduler.runs) ? `调度触发次数：${scheduler.runs}` : '',
      scheduler.last_exit_code != null ? `调度器最近退出码：${scheduler.last_exit_code}` : ''].filter(Boolean);
    if (schedulerMeta.length) detail.append(element('p', 'task-scheduler-meta', schedulerMeta.join('；')));
    row.append(detail);
    return row;
  }

  function render(snapshot) {
    const openIds = new Set([...list.querySelectorAll('.task-row[open]')].map(node => node.dataset.taskId));
    const focusedRow = document.activeElement?.closest('.task-row');
    const focusedId = focusedRow?.dataset.taskId;
    const focusedNote = document.activeElement?.closest('.task-data-note');
    const focusedSelector = focusedNote ? `.task-data-note[data-note-index="${Number(focusedNote.dataset.noteIndex)}"] summary`
      : focusedRow && ['.task-summary', '.task-report summary', '.task-data-scroll', '.task-result-text']
      .find(selector => document.activeElement.matches(selector));
    const reportIds = new Set([...list.querySelectorAll('.task-report[open]')].map(node => node.closest('.task-row').dataset.taskId));
    const openNotes = new Map();
    list.querySelectorAll('.task-data-note[open]').forEach(note => {
      const id = note.closest('.task-row').dataset.taskId;
      if (!openNotes.has(id)) openNotes.set(id, new Set());
      openNotes.get(id).add(note.dataset.noteIndex);
    });
    const tasks = snapshot.tasks;
    const enabled = tasks.filter(task => ['enabled', 'running'].includes(task.scheduler?.state)).length;
    const attention = tasks.filter(task => issuesFor(task).length || ['partial', 'failed', 'timed_out'].includes(task.last_run?.status)).length;
    const active = tasks.filter(task => ['queued', 'running'].includes(task.last_run?.status)).length;
    const counts = [`${tasks.length} 项任务`, `${enabled} 项调度已启用`];
    if (attention) counts.push(`${attention} 项需关注`);
    if (active) counts.push(`${active} 项排队或执行中`);
    overview.textContent = counts.join('，');
    checkedAt.textContent = `状态读取于 ${dateTime(snapshot.checked_at)}`;
    const fragment = document.createDocumentFragment();
    tasks.forEach((task, index) => {
      const id = String(task.id || index);
      const row = renderTask(task, index, openIds.has(id));
      if (reportIds.has(id) && row.querySelector('.task-report')) row.querySelector('.task-report').open = true;
      row.querySelectorAll('.task-data-note').forEach(note => {
        note.open = openNotes.get(id)?.has(note.dataset.noteIndex) || false;
      });
      fragment.append(row);
    });
    if (!tasks.length) fragment.append(element('p', 'tasks-empty', '尚未找到投资定时任务。可刷新状态重试。'));
    // Keep focus, text selection and scroll positions when only the check time changes.
    if (JSON.stringify(lastSnapshot?.tasks) !== JSON.stringify(tasks)) {
      list.replaceChildren(fragment);
      if (focusedSelector) {
        [...list.querySelectorAll('.task-row')].find(node => node.dataset.taskId === focusedId)?.querySelector(focusedSelector)?.focus({ preventScroll: true });
      }
    }
    const warnings = asArray(snapshot.warnings).map(issueText).filter(Boolean);
    warningsBox.replaceChildren();
    warningsBox.hidden = !warnings.length;
    if (warnings.length) {
      const warningList = element('ul');
      warnings.forEach(warning => warningList.append(element('li', '', warning)));
      warningsBox.append(warningList);
    }
  }

  async function loadTasks() {
    if (fetching) return;
    fetching = true;
    lastAttempt = Date.now();
    refresh.disabled = true;
    refresh.textContent = '正在读取…';
    section.setAttribute('aria-busy', 'true');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch('/api/scheduled-tasks', { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const snapshot = await response.json();
      if (!snapshot || !Array.isArray(snapshot.tasks)) throw new Error('Invalid task response');
      render(snapshot);
      lastSnapshot = snapshot;
      errorBox.hidden = true;
      section.classList.remove('tasks-stale');
    } catch {
      errorBox.textContent = lastSnapshot
        ? '状态刷新失败，下方保留上次读取的记录，可能已过期。请点击“重试读取”。'
        : '暂时无法读取任务状态。请检查本地服务后点击“重试读取”；指数估值仍可继续使用。';
      errorBox.hidden = false;
      section.classList.add('tasks-stale');
      if (!lastSnapshot) {
        overview.textContent = '任务状态暂不可用';
        list.replaceChildren(element('p', 'tasks-empty', '尚未读取到任务记录。'));
      }
    } finally {
      clearTimeout(timeout);
      fetching = false;
      refresh.disabled = false;
      refresh.textContent = errorBox.hidden ? '刷新状态' : '重试读取';
      section.setAttribute('aria-busy', 'false');
    }
  }

  refresh.addEventListener('click', loadTasks);
  setInterval(() => { if (!document.hidden) loadTasks(); }, 60000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Date.now() - lastAttempt >= 60000) loadTasks();
  });
  loadTasks();
})();
