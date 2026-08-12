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
    'model.request.completed': `Model request completed${p.elapsed_ms != null ? ` in ${p.elapsed_ms} ms` : ''}`,
    'model.request.failed': `Model request failed${p.error?.message ? `: ${p.error.message}` : ''}`,
    'model.request.interrupted': 'Model request interrupted', 'usage.recorded': `Usage · ${p.input_tokens || 0} input / ${p.output_tokens || 0} output tokens`,
    'permission.decided': `Permission ${p.decision || 'decided'} · ${p.tool_name || 'tool'}`,
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

function renderEvents() {
  $('panel').innerHTML = `<div class="view-toolbar"><div><h2>Events</h2><span class="muted">${current.events.length} recorded events</span></div><div class="segmented" role="group" aria-label="Event detail format"><button data-view="readable" class="${eventView === 'readable' ? 'active' : ''}">Readable</button><button data-view="json" class="${eventView === 'json' ? 'active' : ''}">JSON</button></div></div><div class="event-list">${current.events.map(event => eventView === 'json' ? `<article class="event"><div class="event-head"><span class="seq">#${event.sequence}</span><span class="type">${esc(event.type)}</span><span class="muted">${esc(event.timestamp)}</span></div><pre class="payload">${esc(JSON.stringify(event.payload,null,2))}</pre></article>` : `<article class="event"><div class="event-head"><span class="seq">#${event.sequence}</span><span class="type">${esc(event.type)}</span><span class="muted">${esc(event.timestamp)}</span></div><strong class="event-summary">${esc(readableEvent(event))}</strong>${eventDetails(event)}<details><summary>Show fields</summary><pre class="payload">${esc(JSON.stringify(event.payload,null,2))}</pre></details></article>`).join('')}</div>`;
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
