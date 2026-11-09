// Proxy Rotator v2 — SB Admin 2 dashboard logic

const state = {
  stats: { total: 0, alive: 0, dead: 0, failed: 0, sources: [], top_countries: [], avg_latency_ms: 0 },
  proxies: [],
  logs: [],
  session: { connected: false, proxy: null, exit_ip: null, country: null },
  filterSource: '',
  filterCountry: '',
  filterProtocol: '',
  search: '',
  sortField: 'latency',
  sortOrder: 'asc',
  testingId: null,
  busy: false,
};

// ── Helpers ──────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const api = path => fetch(path).then(r => r.json());
const apiPost = (path, body) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).then(r => r.json());
const apiDelete = path => fetch(path, { method: 'DELETE' }).then(r => r.json());

function toast(msg, type = 'info') {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast toast-' + type;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 4000);
}

// ── Init ─────────────────────────────────────────────────────────────────────
async function init() {
  await Promise.all([refreshStats(), loadProxies(), loadLogs(), loadSession(),
                     loadLocalProxyInfo(), loadSystemProxyInfo()]);
}

// ── Navigation / Views ───────────────────────────────────────────────────────
function switchView(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  document.querySelectorAll('#side-menu li a').forEach(a => a.classList.toggle('active', a.dataset.view === name));
  const titles = { dashboard: 'Dashboard', proxies: 'Proxies', rotate: 'Rotate & Test', logs: 'Logs' };
  $('pageTitle').textContent = titles[name] || 'Dashboard';
  if (name === 'proxies') loadProxies();
  if (name === 'logs') loadLogs();
}

// ── Stats ────────────────────────────────────────────────────────────────────
async function refreshStats() {
  try {
    state.stats = await api('/api/stats');
    $('statAlive').textContent = state.stats.alive || 0;
    $('statTotal').textContent = state.stats.total || 0;
    $('statSources').textContent = (state.stats.sources || []).length || 0;
    $('statDead').textContent = state.stats.dead || 0;
  } catch (e) { console.error('Stats error:', e); }
}

function renderCountries() {
  const list = state.stats.top_countries || [];
  const container = $('countryList');
  if (!list.length) { container.innerHTML = '<p class="text-muted" style="margin:0;">No country data yet.</p>'; return; }
  const max = Math.max(...list.map(c => c.count), 1);
  container.innerHTML = list.map(c => `
    <div class="row" style="margin-bottom: 8px;">
      <div class="col-md-4" style="color: #555;">${c.name || 'Unknown'}</div>
      <div class="col-md-6">
        <div class="progress"><div class="progress-bar progress-bar-info" style="width:${(c.count / max * 100).toFixed(0)}%"></div></div>
      </div>
      <div class="col-md-2" style="text-align: right;"><strong>${c.count}</strong></div>
    </div>
  `).join('');
}

// ── Proxies ──────────────────────────────────────────────────────────────────
async function loadProxies() {
  const params = new URLSearchParams({
    limit: 500, offset: 0, sort: state.sortField, order: state.sortOrder,
    alive_only: ($('checkAliveOnly') || {}).checked !== false ? 'true' : 'false',
  });
  if (state.filterSource) params.set('source', state.filterSource);
  if (state.filterCountry) params.set('country', state.filterCountry);
  if (state.filterProtocol) params.set('protocol', state.filterProtocol);
  if (state.search) params.set('search', state.search);
  try {
    const r = await api('/api/proxies?' + params);
    state.proxies = r.proxies || [];
    renderProxies();
  } catch (e) { console.error(e); }
}

function renderProxies() {
  const tb = $('proxyTable');
  if (!state.proxies.length) {
    tb.innerHTML = `<tr><td colspan="9"><div class="text-center text-muted" style="padding: 30px;">
      <i class="fa fa-search" style="font-size: 28px;"></i><br>
      No proxies found. Click <strong>Fetch New</strong> to populate the pool.
    </div></td></tr>`;
    $('proxyCount').textContent = '0 proxies shown';
    return;
  }
  tb.innerHTML = state.proxies.map(p => `
    <tr>
      <td><span class="badge ${p.is_alive ? 'badge-success' : 'badge-danger'}">${p.is_alive ? 'Alive' : 'Dead'}</span></td>
      <td class="mono" style="font-weight: bold;">${p.ip}:${p.port}</td>
      <td><span class="badge badge-info">${p.protocol}</span></td>
      <td>${p.anonymity || '—'}</td>
      <td>${p.country || '—'}</td>
      <td style="color: ${p.is_alive && p.latency_ms ? (p.latency_ms < 500 ? '#5cb85c' : (p.latency_ms < 1500 ? '#f0ad4e' : '#d9534f')) : '#888'};">
        ${p.is_alive && p.latency_ms ? p.latency_ms.toFixed(0) + 'ms' : '—'}
      </td>
      <td>${p.source || '—'}</td>
      <td>${p.fail_count > 0 ? '<span style="color:#d9534f">' + p.fail_count + '</span>' : '—'}</td>
      <td>
        <button class="btn btn-default btn-sm" onclick="testProxy(${p.id})" ${state.testingId === p.id ? 'disabled' : ''} title="Test">
          <i class="fa ${state.testingId === p.id ? 'fa-spinner fa-spin' : 'fa-play'}"></i>
        </button>
        <button class="btn btn-success btn-sm" onclick="connectProxy(${p.id})" ${!p.is_alive ? 'disabled' : ''} title="Connect via this proxy">
          <i class="fa fa-plug"></i>
        </button>
        <button class="btn btn-danger btn-sm" onclick="deleteProxy(${p.id})" title="Delete" style="margin-left:4px;">
          <i class="fa fa-trash"></i>
        </button>
      </td>
    </tr>`).join('');
  $('proxyCount').textContent = state.proxies.length + ' proxies shown';
}

async function testProxy(id) {
  state.testingId = id;
  try {
    const r = await apiPost('/api/proxies/' + id + '/test');
    if (r.alive) toast('OK: ' + r.proxy_url + ' → ' + r.exit_ip + ' (' + r.latency_ms + 'ms)', 'success');
    else toast('FAIL: ' + r.proxy_url + ' — ' + (r.error || ''), 'error');
    await Promise.all([loadProxies(), refreshStats()]);
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  state.testingId = null;
}

async function deleteProxy(id) {
  if (!confirm('Delete proxy #' + id + '?')) return;
  await apiDelete('/api/proxies/' + id);
  await Promise.all([loadProxies(), refreshStats()]);
  toast('Proxy deleted', 'success');
}

// ── Fetch / Validate / Clear ──────────────────────────────────────────────────
async function fetchProxies() {
  if (state.busy) return;
  state.busy = true;
  try {
    const r = await apiPost('/api/proxies/fetch?limit=500');
    toast('Fetched ' + r.fetched + ' proxies', 'success');
    await Promise.all([refreshStats(), loadProxies()]);
  } catch (e) { toast('Fetch failed: ' + e.message, 'error'); }
  state.busy = false;
}

async function validateAll() {
  if (state.busy) return;
  state.busy = true;
  try {
    const r = await apiPost('/api/proxies/validate');
    toast('Validated ' + r.validated + ' → ' + r.new_alive + ' new alive', 'success');
    await Promise.all([refreshStats(), loadProxies()]);
  } catch (e) { toast('Validation failed: ' + e.message, 'error'); }
  state.busy = false;
}

async function clearDead() {
  if (!confirm('Remove all dead / high-fail proxies?')) return;
  const r = await apiDelete('/api/proxies/clear-dead');
  toast('Removed ' + r.removed + ' dead proxies', 'success');
  await Promise.all([refreshStats(), loadProxies()]);
}

async function clearAll() {
  if (!confirm('DELETE ALL proxies? This cannot be undone!')) return;
  const r = await apiDelete('/api/proxies/clear-all');
  toast(r.message || 'All proxies cleared', 'success');
  await Promise.all([refreshStats(), loadProxies()]);
}

function exportCSV() {
  if (!state.proxies.length) { toast('No proxies to export', 'error'); return; }
  const h = ['ID', 'IP', 'Port', 'Protocol', 'Anonymity', 'Country', 'Source', 'Latency', 'Alive', 'Fails'];
  const rows = [h, ...state.proxies.map(p => [p.id, p.ip, p.port, p.protocol, p.anonymity, p.country, p.source, p.latency_ms, p.is_alive, p.fail_count])];
  const csv = rows.map(r => r.map(v => '"' + v + '"').join(',')).join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = 'proxies_' + new Date().toISOString().slice(0, 10) + '.csv';
  a.click();
}

// ── Connect / Session ────────────────────────────────────────────────────────
async function loadSession() {
  try {
    state.session = await api('/api/session');
    renderSession();
    await loadSessionProxyOptions();
    const ar = document.getElementById('autoRotateState');
    if (ar) ar.textContent = state.session.auto_rotate ? 'ON' : 'OFF';

    // Proxy status banner on the dashboard
    const banner = document.getElementById('proxyBanner');
    const bannerText = document.getElementById('proxyBannerText');
    const connected = !!(state.session && state.session.connected && state.session.proxy);
    if (banner) banner.style.display = connected ? 'block' : 'none';
    if (bannerText && connected) {
      bannerText.innerHTML = 'Exit IP <span class="mono">' + (state.session.exit_ip || '—') + '</span> · ' +
        'Lokasi: ' + (state.session.country || '—') + ' · Auto-rotate: ' + (state.session.auto_rotate ? 'ON' : 'OFF');
    }
  } catch (e) { console.error(e); }
}

function hideProxyBanner() {
  const b = document.getElementById('proxyBanner');
  if (b) b.style.display = 'none';
}

async function loadLocalProxyInfo() {
  try {
    const p = await api('/api/local-proxy');
    const el = $('localProxyUrl');
    if (el) el.textContent = (p.url || '').replace(/^http:\/\//, '') + (p.running ? '' : ' (off)');
  } catch (e) { console.error(e); }
}

async function loadSystemProxyInfo() {
  try {
    const s = await api('/api/system-proxy');
    const el = $('systemProxyState');
    const on = $('btnSystemProxyOn');
    const off = $('btnSystemProxyOff');
    if (!el) return;
    if (!s.available) {
      el.textContent = 'System proxy tidak tersedia (gsettings tidak ditemukan).';
      if (on) on.disabled = true;
      if (off) off.disabled = true;
      return;
    }
    el.textContent = s.enabled
      ? 'System proxy: AKTIF (' + s.host + ':' + s.port + ') — browser ikut lokasi proxy.'
      : 'System proxy: nonaktif (mode: ' + (s.mode || '-') + ').';
    if (on) on.disabled = s.enabled;
    if (off) off.disabled = !s.enabled;
  } catch (e) { console.error(e); }
}

async function setSystemProxy(enabled) {
  const r = await apiPost('/api/system-proxy', { enabled: enabled });
  if (r.ok) {
    toast(enabled
      ? 'Proxy browser AKTIF — reload/buka ulang tab browser kamu'
      : 'Proxy browser dimatikan', 'success');
  } else {
    toast('Gagal: ' + (r.error || 'unknown'), 'error');
  }
  await loadSystemProxyInfo();
}

async function setAutoRotate() {
  const enabled = !(state.session && state.session.auto_rotate);
  const r = await apiPost('/api/session/auto-rotate', { enabled: enabled });
  if (r.ok) {
    state.session.auto_rotate = r.enabled;
    const st = document.getElementById('autoRotateState');
    if (st) st.textContent = r.enabled ? 'ON' : 'OFF';
    toast(r.enabled
      ? 'Auto-rotate ON — proxy session akan terus berpindah'
      : 'Auto-rotate OFF', 'success');
  } else {
    toast('Gagal: ' + (r.error || 'unknown'), 'error');
  }
}

async function loadSessionProxyOptions() {
  const r = await api('/api/proxies?limit=100&alive_only=true&sort=latency&order=asc');
  const sel = $('sessionProxySelect');
  sel.innerHTML = '<option value="">— Select alive proxy —</option>';
  (r.proxies || []).forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = p.ip + ':' + p.port + ' (' + (p.country || 'Unknown') + ') — ' + (p.latency_ms ? p.latency_ms.toFixed(0) + 'ms' : '');
    sel.appendChild(opt);
  });
}

function renderSession() {
  const s = state.session;
  const info = $('sessionInfo');
  const disconnectBtn = $('btnSessionDisconnect');
  const connectBtn = $('btnSessionConnect');
  if (!s || !s.connected) {
    info.innerHTML = '<p class="text-muted" style="margin:0;">No active proxy session.</p>';
    disconnectBtn.style.display = 'none';
    connectBtn.style.display = 'inline-block';
    return;
  }
  disconnectBtn.style.display = 'inline-block';
  connectBtn.style.display = 'none';
  info.innerHTML = `
    <div style="margin-bottom: 8px;">
      <span class="badge badge-success">Connected</span>
    </div>
    <div><span class="text-muted">Proxy:</span> <span class="mono" style="font-weight:bold;">${s.proxy.ip}:${s.proxy.port}</span></div>
    <div><span class="text-muted">Protocol:</span> ${s.proxy.protocol}</div>
    <div><span class="text-muted">Exit IP:</span> <span class="mono" style="font-weight:bold;">${s.exit_ip || '—'}</span></div>
    <div><span class="text-muted">Location:</span> ${s.country || '—'}</div>
    <div><span class="text-muted">Latency:</span> ${s.proxy.latency_ms ? s.proxy.latency_ms.toFixed(0) + 'ms' : '—'}</div>
  `;
}

async function sessionConnect() {
  const val = $('sessionProxySelect').value;
  if (!val) { toast('Pilih proxy dulu dari daftar alive', 'error'); return; }
  const r = await apiPost('/api/session/connect', { proxy_id: parseInt(val) });
  if (r.connected) {
    toast('Connected via ' + r.proxy + ' → Exit IP: ' + (r.exit_ip || '—'), 'success');
  } else {
    toast('Connect gagal: ' + (r.error || 'unknown'), 'error');
  }
  await loadSession();
}

async function sessionDisconnect() {
  const r = await apiPost('/api/session/disconnect', {});
  toast('Disconnected', 'success');
  await loadSession();
}

async function connectProxy(id) {
  const r = await apiPost('/api/session/connect', { proxy_id: id });
  if (r.connected) {
    toast('Connected via ' + r.proxy + ' → Exit IP: ' + (r.exit_ip || '—'), 'success');
  } else {
    toast('Connect gagal: ' + (r.error || 'unknown'), 'error');
  }
  await loadSession();
}

// ── Rotate ───────────────────────────────────────────────────────────────────
async function rotateTest() {
  const url = $('testUrl').value || 'https://api.ipify.org?format=json';
  const method = $('testMethod').value;
  const r = await api('/api/rotate/test?url=' + encodeURIComponent(url) + '&method=' + method);
  $('rotateResult').style.display = 'block';
  if (r.error || r.status === 0) {
    $('rotateBody').textContent = 'Error: ' + (r.error || JSON.stringify(r));
  } else {
    $('rotateBody').textContent = JSON.stringify({ status: r.status, proxy: r.proxy, exit_ip: (r.body || '').trim().replace(/"/g, ''), latency_ms: r.latency_ms }, null, 2);
  }
}

// ── Logs ─────────────────────────────────────────────────────────────────────
async function loadLogs() {
  try {
    const r = await api('/api/logs?limit=200');
    state.logs = r.logs || [];
    renderLogs();
  } catch (e) { console.error(e); }
}

function renderLogs() {
  const c = $('logTable');
  if (!state.logs.length) { c.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No activity yet</td></tr>'; return; }
  c.innerHTML = state.logs.map(l => `
    <tr>
      <td class="mono">${new Date(l.created_at * 1000).toLocaleString()}</td>
      <td><span class="badge badge-primary">${l.action}</span></td>
      <td class="mono">${l.proxy || '—'}</td>
      <td class="mono">${(l.target_ip || '—').slice(0, 40)}</td>
      <td>${(l.result || '—').slice(0, 60)}</td>
    </tr>`).join('');
}

// ── Sidebar navigation ───────────────────────────────────────────────────────
document.querySelectorAll('#side-menu a').forEach(a => {
  if (!a.dataset.view) return;  // normal links (e.g. /proof) navigate as usual
  a.addEventListener('click', (e) => {
    e.preventDefault();
    switchView(a.dataset.view);
  });
});

// ── Logout ───────────────────────────────────────────────────────────────────
document.getElementById('logoutBtn').addEventListener('click', () => toast('Logout clicked (demo only)', 'info'));

// ── Start ────────────────────────────────────────────────────────────────────
init();
