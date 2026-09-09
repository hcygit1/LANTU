const $ = id => document.getElementById(id);
let current = null;
let activeTab = 'events';
let eventView = 'readable';
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function loadSessions() {
  const response = await fetch('/api/sessions');
  if (!response.ok) throw new Error('Unable to load sessions');
  const items = await response.json();
  $('sessions').innerHTML = items.length ? items.map(s => `<button class="session" data-id="${esc(s.id)}">${esc(s.id)}</button>`).join('') : '<p class="muted">No sessions yet.</p>';
  document.querySelectorAll('.session').forEach(button => button.onclick = () => loadSession(button.dataset.id));
}

async function loadSession(id) {
  const response = await fetch(`/api/session/${encodeURIComponent(id)}`);
  if (!response.ok) throw new Error('Session not found');
  current = await response.json();
  $('empty').hidden = true; $('search-results').hidden = true; $('details').hidden = false;
  $('session-title').textContent = id;
  $('status').textContent = `Status: ${current.diagnosis.status} | ${current.events.length} events | ${current.tasks.length} tasks`;
  document.querySelectorAll('.session').forEach(b => b.classList.toggle('active', b.dataset.id === id));
  renderTab(activeTab);
}

function renderTab(tab) {
  activeTab = tab;
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  if (!current) return;
  if (tab === 'events') renderEvents();
  if (tab === 'tasks') renderTasks();
  if (tab === 'actions') renderActions();
  if (tab === 'cache') renderCache();
  if (tab === 'diagnosis') renderDiagnosis();
  if (tab === 'evidence') renderEvidence();
}

function readableEvent(event) {
  const p = event.payload || {};
  const summaries = {
    'session.created': 'Session created', 'runtime.started': `Runtime started (${p.mode || 'unknown mode'})`,
    'runtime.stopped': `Runtime stopped${p.reason ? `: ${p.reason}` : ''}`, 'turn.started': 'Turn started',
    'turn.completed': `Turn completed${p.iteration_count ? ` after ${p.iteration_count} iterations` : ''}`,
    'turn.interrupted': `Turn interrupted${p.reason ? `: ${p.reason}` : ''}`,
    'model.request.started': `Model request started${p.model ? ` · ${p.model}` : ''}`,
    'model.request.prepared': `Model payload prepared${p.payload?.digest ? ` · ${p.payload.digest.slice(0, 8)}` : ''}`,
    'model.request.completed': `Model request completed${p.elapsed_ms != null ? ` in ${p.elapsed_ms} ms` : ''}`,
    'model.request.failed': `Model request failed${p.error?.message ? `: ${p.error.message}` : ''}`,
    'model.request.interrupted': 'Model request interrupted', 'usage.recorded': `Usage · ${p.input_tokens || 0} input / ${p.output_tokens || 0} output tokens`,
    'permission.decided': `Permission ${p.decision || 'decided'} · ${p.tool_name || 'tool'}`,
    'context.tool_results_compacted': `Stale tool results compacted · ${(p.replacements || []).length} replaced`,
    'context.window.rolled_over': `Context window rolled over · ${p.parent_window_id || '?'} → ${p.window_id || '?'}`,
    'error.occurred': `Error${p.message ? `: ${p.message}` : ''}`,
  };
  if (summaries[event.type]) return summaries[event.type];
  if (event.type === 'message.created') return `${p.role || 'message'} message${p.content ? ` · ${String(p.content).slice(0, 120)}` : ''}`;
  if (event.type === 'tool.started') return `Tool started · ${p.tool_name || 'unknown tool'}`;
  if (event.type === 'tool.completed') return `Tool completed · ${p.tool_name || 'unknown tool'}${p.elapsed_ms != null ? ` in ${p.elapsed_ms} ms` : ''}`;
  if (event.type === 'tool.failed') return `Tool failed · ${p.tool_name || 'unknown tool'}`;
  if (event.type === 'tool.interrupted') return `Tool interrupted · ${p.tool_name || 'unknown tool'}`;
  return event.type;
}

function eventDetails(event) {
  const p = event.payload || {};
  const content = p.content || p.output || p.error?.message;
  return content ? `<p class="event-content">${esc(String(content).slice(0, 800))}</p>` : '';
}

function renderEvent(event) {
  if (eventView === 'json') {
    return `<article class="event"><div class="event-head"><span class="seq">#${event.sequence}</span><span class="type">${esc(event.type)}</span><span class="muted">${esc(event.timestamp)}</span></div><pre class="payload">${esc(JSON.stringify(event.payload,null,2))}</pre></article>`;
  }
  return `<article class="event"><div class="event-head"><span class="seq">#${event.sequence}</span><span class="type">${esc(event.type)}</span>${messageClassification(event)}<span class="muted">${esc(event.timestamp)}</span></div><strong class="event-summary">${esc(readableEvent(event))}</strong>${eventDetails(event)}<details><summary>Show fields</summary><pre class="payload">${esc(JSON.stringify(event.payload,null,2))}</pre></details></article>`;
}

function fallbackWindows(events) {
  return [{ window_id: 'window_legacy', parent_window_id: null, start_sequence: events[0]?.sequence || 0, end_sequence: events.at(-1)?.sequence || 0, is_active: true, events }];
}

function renderWindow(window, index, total) {
  const label = `Window ${String(index + 1).padStart(2, '0')}`;
  const parent = window.parent_window_id ? `<span class="window-parent">from ${esc(window.parent_window_id)}</span>` : '<span class="window-parent">Session start</span>';
  return `<details class="window-group" ${window.is_active ? 'open' : ''}><summary><span class="window-index">${label}</span><span class="window-id" title="${esc(window.window_id)}">${esc(window.window_id)}</span>${window.is_active ? '<span class="badge completed">Current</span>' : ''}<span class="window-count">${window.events.length} events</span><span class="window-range">#${window.start_sequence}–#${window.end_sequence}</span>${parent}</summary><div class="window-events">${window.events.map(renderEvent).join('')}</div></details>`;
}

function renderEvents() {
  const windows = current.windows?.length ? current.windows : fallbackWindows(current.events);
  $('panel').innerHTML = `<div class="view-toolbar"><div><h2>Events</h2><span class="muted">${current.events.length} recorded events · ${windows.length} window${windows.length === 1 ? '' : 's'}</span></div><div class="segmented" role="group" aria-label="Event detail format"><button data-view="readable" class="${eventView === 'readable' ? 'active' : ''}">Readable</button><button data-view="json" class="${eventView === 'json' ? 'active' : ''}">JSON</button></div></div><div class="window-list">${windows.map((window, index) => renderWindow(window, index, windows.length)).join('')}</div>`;
  document.querySelectorAll('[data-view]').forEach(button => button.onclick = () => { eventView = button.dataset.view; renderEvents(); });
}

function renderTasks() {
  $('panel').innerHTML = `<div class="view-toolbar"><div><h2>Tasks</h2><span class="muted">Grouped by turn lifecycle</span></div></div><div class="task-list full">${current.tasks.length ? current.tasks.map(task => `<article class="task"><div><strong>${esc(task.task_id)}</strong><span class="badge">Sequences ${task.start_sequence}-${task.end_sequence}</span></div><p class="muted">${task.events.length} events</p><button class="text-button" data-task-seq="${task.start_sequence}">View events</button></article>`).join('') : '<p class="muted">No tasks detected.</p>'}</div>`;
  document.querySelectorAll('[data-task-seq]').forEach(button => button.onclick = () => { activeTab = 'events'; renderTab('events'); });
}

function renderActions() {
  const nodes = current.actions.flatMap(item => item.graph.nodes || []);
  $('panel').innerHTML = `<div class="view-toolbar"><div><h2>Actions</h2><span class="muted">Normalized action graph</span></div></div>${nodes.length ? `<div class="action-list">${nodes.map(node => `<article class="action"><div><span class="type">${esc(node.kind)}</span><span class="badge ${esc(node.status)}">${esc(node.status)}</span></div><strong>${esc(node.action_id)}</strong><span class="muted">Sequence #${node.start_sequence}${node.end_sequence !== node.start_sequence ? ` → #${node.end_sequence}` : ''}</span></article>`).join('')}</div>` : '<p class="muted">No actions detected.</p>'}`;
}

function cacheChangeLabel(change) {
  return String(change || 'unknown').replaceAll('_', ' ');
}

function renderCache() {
  const report = current.cache || { calls: [] };
  const calls = report.calls || [];
  const totalPrompt = calls.reduce((sum, call) => sum + (call.prompt_tokens || 0), 0);
  const totalRead = calls.reduce((sum, call) => sum + (call.cache_read_tokens || 0), 0);
  const changed = calls.filter(call => !['cold_start', 'unchanged', 'append_only'].includes(call.change)).length;
  const hitRate = totalPrompt ? totalRead / totalPrompt : 0;
  const breakpoints = report.breakpoints_by_kind || {};
  const breakpointText = Object.entries(breakpoints).map(([kind, count]) => `${esc(kind)} ${count}`).join(' · ') || 'None';
  $('panel').innerHTML = `<div class="view-toolbar"><div><h2>Cache</h2><span class="muted">Prefix stability across model calls</span></div></div><div class="cache-stats"><div><span class="muted">Calls</span><strong>${calls.length}</strong></div><div><span class="muted">Hit rate</span><strong>${(hitRate * 100).toFixed(1)}%</strong></div><div><span class="muted">Cache read</span><strong>${totalRead.toLocaleString()}</strong></div><div><span class="muted">Changed calls</span><strong>${changed}</strong></div></div><div class="cache-breakpoints"><span class="muted">Breakpoints by message kind</span><strong>${breakpointText}</strong></div>${calls.length ? `<div class="cache-list">${calls.map(call => { const counts = Object.entries(call.message_kind_counts || {}).map(([kind, count]) => `${esc(kind)} ${count}`).join(' · ') || 'None'; const divergence = call.first_divergence_message == null ? 'None' : `Message #${call.first_divergence_message} · ${esc(call.first_divergence_kind || 'unknown')}${call.first_divergence_appendix_key ? ` · key=${esc(call.first_divergence_appendix_key)}` : ''}`; return `<details class="cache-call"><summary><span class="seq">#${call.sequence}</span><span class="type">${esc(call.call_kind)}</span><span class="badge cache-${esc(call.change)}">${esc(cacheChangeLabel(call.change))}</span><span class="cache-rate">${((call.cache_hit_rate || 0) * 100).toFixed(1)}%</span></summary><div class="cache-detail"><div><span class="muted">Model</span><strong>${esc(call.provider)} / ${esc(call.model || 'unknown')}</strong></div><div><span class="muted">Common messages</span><strong>${call.common_message_count} (${(call.common_message_chars || 0).toLocaleString()} chars)</strong></div><div><span class="muted">First divergence</span><strong>${divergence}</strong></div><div><span class="muted">Message kinds</span><strong>${counts}</strong></div><div><span class="muted">Tokens</span><strong>${(call.prompt_tokens || 0).toLocaleString()} prompt · ${(call.cache_read_tokens || 0).toLocaleString()} cached · ${(call.cache_creation_tokens || 0).toLocaleString()} created</strong></div><div><span class="muted">Call ID</span><code>${esc(call.model_call_id)}</code></div></div></details>`; }).join('')}</div>` : '<p class="muted">No model cache data recorded.</p>'}`;
}

function messageClassification(event) {
  if (event.type !== 'message.created') return '';
  const p = event.payload || {};
  const kind = p.kind || 'frozen';
  const key = p.appendix_key ? ` · key=${esc(p.appendix_key)}` : '';
  return `<span class="badge message-kind-${esc(kind)}">${esc(kind)}${key}</span>`;
}

function renderDiagnosis() {
  const findings = current.diagnosis.findings || [];
  $('panel').innerHTML = `<div class="view-toolbar"><div><h2>Diagnosis</h2><span class="muted">${findings.length} findings</span></div></div>${findings.length ? findings.map(f => `<article class="finding ${esc(f.severity)}"><div><strong>${esc(f.code)}</strong><span class="badge">${esc(f.severity)}</span></div><p>${esc(f.message)}</p><span class="muted">Evidence: ${f.evidence_sequences.join(', ')}</span></article>`).join('') : '<p class="muted">No failures or incomplete operations.</p>'}`;
}

function renderEvidence() {
  const evidence = current.evidence || [];
  $('panel').innerHTML = `<div class="view-toolbar"><div><h2>Evidence</h2><span class="muted">${evidence.length} HTTP links</span></div></div>${evidence.length ? evidence.map(item => `<article class="evidence"><div><strong>${esc(item.model_call_id)}</strong><span class="badge ${esc(item.confidence)}">${esc(item.confidence)}</span></div><p class="muted">Journal sequence: ${item.journal_sequence}</p></article>`).join('') : '<p class="muted">No capture evidence.</p>'}`;
}

document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => renderTab(b.dataset.tab));
$('search').onkeydown = async event => { if (event.key !== 'Enter' || !event.target.value.trim()) return; const rows = await fetch(`/api/search?query=${encodeURIComponent(event.target.value.trim())}`).then(r => r.json()); $('details').hidden = true; $('empty').hidden = true; $('search-results').hidden = false; $('results').innerHTML = rows.map(row => `<article class="event"><strong>${esc(row.session_id)}</strong> <span class="type">${esc(row.event.type)}</span><strong class="event-summary">${esc(readableEvent(row.event))}</strong></article>`).join('') || '<p class="muted">No matches.</p>'; };
loadSessions().catch(error => $('sessions').innerHTML = `<p class="muted">${esc(error.message)}</p>`);
