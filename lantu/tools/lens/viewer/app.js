const $ = id => document.getElementById(id);
let current = null;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function loadSessions() {
  const items = await fetch('/api/sessions').then(r => r.json());
  $('sessions').innerHTML = items.length ? items.map(s => `<button class="session" data-id="${esc(s.id)}">${esc(s.id)}</button>`).join('') : '<p class="muted">No sessions yet.</p>';
  document.querySelectorAll('.session').forEach(button => button.onclick = () => loadSession(button.dataset.id));
}
async function loadSession(id) {
  current = await fetch(`/api/session/${encodeURIComponent(id)}`).then(r => r.json());
  $('empty').hidden = true; $('search-results').hidden = true; $('details').hidden = false;
  $('session-title').textContent = id; $('status').textContent = `Status: ${current.diagnosis.status} | ${current.events.length} events | ${current.tasks.length} tasks`;
  document.querySelectorAll('.session').forEach(b => b.classList.toggle('active', b.dataset.id === id));
  renderTab('events');
}
function renderTab(tab) {
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  if (!current) return;
  if (tab === 'events') $('panel').innerHTML = current.events.map(e => `<article class="event"><div class="event-head"><span class="seq">#${e.sequence}</span><span class="type">${esc(e.type)}</span><span class="muted">${esc(e.timestamp)}</span></div><pre class="payload">${esc(JSON.stringify(e.payload,null,2))}</pre></article>`).join('');
  if (tab === 'tasks') $('panel').innerHTML = current.tasks.map(t => `<article class="task"><strong>${esc(t.task_id)}</strong> <span class="badge">${t.start_sequence}-${t.end_sequence}</span><p class="muted">${t.events.length} events</p></article>`).join('');
  if (tab === 'diagnosis') $('panel').innerHTML = current.diagnosis.findings.length ? current.diagnosis.findings.map(f => `<article class="finding ${esc(f.severity)}"><strong>${esc(f.code)}</strong> <span class="badge">${esc(f.severity)}</span><p>${esc(f.message)}</p><span class="muted">Evidence: ${f.evidence_sequences.join(', ')}</span></article>`).join('') : '<p class="muted">No failures or incomplete operations.</p>';
  if (tab === 'evidence') $('panel').innerHTML = current.evidence.length ? current.evidence.map(e => `<article class="evidence"><strong>${esc(e.model_call_id)}</strong> <span class="badge">${esc(e.confidence)}</span><p class="muted">Journal sequence: ${e.journal_sequence}</p></article>`).join('') : '<p class="muted">No capture evidence.</p>';
}
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => renderTab(b.dataset.tab));
$('search').onkeydown = async event => { if (event.key !== 'Enter' || !event.target.value.trim()) return; const rows = await fetch(`/api/search?query=${encodeURIComponent(event.target.value.trim())}`).then(r => r.json()); $('details').hidden = true; $('empty').hidden = true; $('search-results').hidden = false; $('results').innerHTML = rows.map(row => `<article class="event"><strong>${esc(row.session_id)}</strong> <span class="type">${esc(row.event.type)}</span><pre class="payload">${esc(JSON.stringify(row.event.payload,null,2))}</pre></article>`).join('') || '<p class="muted">No matches.</p>'; };
loadSessions().catch(error => $('sessions').innerHTML = `<p class="muted">${esc(error.message)}</p>`);
