import { get, post } from '../api.js';
import { emptyState, errorBox, esc, skeleton, timeAgo, toast } from '../ui.js';

function formatDurationSec(seconds) {
  var s = Number(seconds || 0);
  if (!isFinite(s) || s <= 0) return '0s';
  if (s < 60) return s.toFixed(1) + 's';
  if (s < 3600) return Math.round(s / 60) + 'm (' + Math.round(s) + 's)';
  if (s < 86400) return (s / 3600).toFixed(1) + 'h';
  return (s / 86400).toFixed(1) + 'd (' + Math.round(s) + 's)';
}

function computeLiveRetention(record) {
  if (!record) return 1.0;
  var stability = Number(record.stability || 86400);
  if (record.last_recalled_at && isFinite(stability) && stability > 0) {
    var ts = Date.parse(record.last_recalled_at);
    if (!isNaN(ts)) {
      var elapsedSec = Math.max(0, (Date.now() - ts) / 1000.0);
      var r = Math.exp(-elapsedSec / stability);
      if (isFinite(r)) {
        return Math.max(0, Math.min(1, r));
      }
    }
  }
  if (typeof record.retention === 'number' && isFinite(record.retention)) {
    return Math.max(0, Math.min(1, record.retention));
  }
  return 1.0;
}

function retentionColor(r) {
  if (r >= 0.70) return '#10b981';
  if (r >= 0.35) return '#f59e0b';
  return '#ef4444';
}

function statusBadgeStyle(status) {
  var st = String(status || 'ACTIVE').toUpperCase();
  if (st === 'ACTIVE') {
    return 'background:rgba(16,185,129,0.14);color:#34d399;border:1px solid rgba(16,185,129,0.32)';
  }
  if (st === 'CONFLICTING') {
    return 'background:rgba(245,158,11,0.16);color:#fbbf24;border:1px solid rgba(245,158,11,0.38)';
  }
  return 'background:rgba(148,163,184,0.14);color:#94a3b8;border:1px solid rgba(148,163,184,0.28)';
}

function shortHash(h) {
  var s = String(h || '');
  if (!s) return '—';
  if (s.length <= 16) return s;
  return s.slice(0, 10) + '…' + s.slice(-6);
}

export async function render(targetContainer) {
  var view = targetContainer || document.getElementById('view');
  if (!view) return;

  var state = {
    projectId: '',
    projects: [],
    activeTab: 'episodic',
    episodic: [],
    semantic: [],
    playbooks: [],
    allEpisodic: [],
    allSemantic: [],
    allPlaybooks: [],
    ledger: [],
    integrity: null,
    episodicQuery: '',
    minRetention: 0.05,
    semanticStatus: 'ALL',
    semanticSubject: '',
    playbookQuery: '',
    showForm: false
  };

  view.innerHTML = [
    '<div class="ky-memory-os fade-in" style="max-width:1180px;margin:0 auto;padding:8px 0 56px">',
    '  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:18px">',
    '    <div>',
    '      <div class="ky-eyebrow" style="color:var(--text-3);margin-bottom:4px">3-TIER BIOLOGICALLY INSPIRED MEMORY ARCHITECTURE</div>',
    '      <h1 style="font-size:23px;font-weight:700;margin:0;letter-spacing:-0.02em">Memory OS &amp; Cryptographic Ledger</h1>',
    '      <p style="margin:5px 0 0;font-size:13px;color:var(--text-2)">Ebbinghaus episodic decay R(t) = e<sup>-t/S</sup>, hybrid semantic contradiction &amp; belief propagation, Laplace-smoothed playbooks, and SHA-256 tamper-evident audit chains.</p>',
    '    </div>',
    '    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">',
    '      <label for="project-select" style="display:inline-flex;align-items:center;gap:8px;padding:6px 12px;background:var(--surface-2, var(--bg2));border:1px solid var(--border);border-radius:8px;font-size:12.5px;color:var(--text-2)">',
    '        <span style="font-family:var(--font-mono);font-size:11px;text-transform:uppercase;color:var(--text-3)">Project</span>',
    '        <select id="project-select" name="project_id" aria-label="Select Project for Memory Inspector" style="border:none;background:transparent;outline:none;color:var(--text-1);font-weight:600;font-size:13px;cursor:pointer">',
    '          <option value="">Loading…</option>',
    '        </select>',
    '      </label>',
    '      <button type="button" id="mem-verify-btn" class="ky-btn ky-btn-ghost ky-btn-sm" style="border:1px solid var(--border);display:inline-flex;align-items:center;gap:6px;font-family:var(--font-mono);font-size:12px">',
    '        <span aria-hidden="true">&#x1F6E1;</span>',
    '        <span>Verify SHA-256 Ledger</span>',
    '      </button>',
    '      <button type="button" id="mem-refresh-btn" class="ky-btn ky-btn-ghost ky-btn-sm" style="border:1px solid var(--border);font-size:12px" title="Reload all memory tiers">Refresh</button>',
    '    </div>',
    '  </div>',
    '',
    '  <div id="mem-integrity-banner" role="status" aria-live="polite" style="margin-bottom:18px"></div>',
    '',
    '  <div id="mem-kpi-strip" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:20px"></div>',
    '',
    '  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;border-bottom:1px solid var(--border);margin-bottom:18px;padding-bottom:10px">',
    '    <div class="tabs" id="category-tabs" role="tablist" aria-label="Memory Architecture Tiers" style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">',
    '      <button type="button" role="tab" id="mem-tab-episodic" tabindex="0" aria-selected="true" aria-controls="memory-list" class="tab-btn ky-btn ky-btn-primary ky-btn-xs" data-tab="episodic" data-cat="episodic" style="font-size:12.5px;padding:6px 12px">1. Episodic Memory</button>',
    '      <button type="button" role="tab" id="mem-tab-semantic" tabindex="-1" aria-selected="false" aria-controls="memory-list" class="tab-btn ky-btn ky-btn-ghost ky-btn-xs" data-tab="semantic" data-cat="semantic" style="font-size:12.5px;padding:6px 12px">2. Semantic Constraints</button>',
    '      <button type="button" role="tab" id="mem-tab-procedural" tabindex="-1" aria-selected="false" aria-controls="memory-list" class="tab-btn ky-btn ky-btn-ghost ky-btn-xs" data-tab="procedural" data-cat="procedural" style="font-size:12.5px;padding:6px 12px">3. Procedural Playbooks</button>',
    '      <button type="button" role="tab" id="mem-tab-ledger" tabindex="-1" aria-selected="false" aria-controls="memory-list" class="tab-btn ky-btn ky-btn-ghost ky-btn-xs" data-tab="ledger" data-cat="ledger" style="font-size:12.5px;padding:6px 12px">4. SHA-256 Audit Chain</button>',
    '    </div>',
    '    <button type="button" id="mem-toggle-create-btn" aria-expanded="false" aria-controls="mem-create-panel" class="ky-btn ky-btn-primary ky-btn-xs" style="padding:6px 12px;font-size:12px">+ Add Record</button>',
    '  </div>',
    '',
    '  <div id="mem-create-panel" style="display:none;margin-bottom:18px"></div>',
    '  <div id="mem-filter-bar" style="margin-bottom:16px"></div>',
    '  <div class="list" id="memory-list" role="tabpanel" aria-labelledby="mem-tab-episodic">',
    '    ' + skeleton(4),
    '  </div>',
    '</div>'
  ].join('\n');

  var selectEl = document.getElementById('project-select');
  var listEl = document.getElementById('memory-list');
  var bannerEl = document.getElementById('mem-integrity-banner');
  var kpiEl = document.getElementById('mem-kpi-strip');
  var filterBarEl = document.getElementById('mem-filter-bar');
  var createPanelEl = document.getElementById('mem-create-panel');
  var toggleCreateBtn = document.getElementById('mem-toggle-create-btn');
  var verifyBtn = document.getElementById('mem-verify-btn');
  var refreshBtn = document.getElementById('mem-refresh-btn');
  var tabButtons = Array.prototype.slice.call(view.querySelectorAll('.tab-btn'));

  function syncTabVisuals() {
    tabButtons.forEach(function (btn) {
      var isAct = btn.getAttribute('data-tab') === state.activeTab;
      btn.setAttribute('aria-selected', isAct ? 'true' : 'false');
      btn.setAttribute('tabindex', isAct ? '0' : '-1');
      btn.classList.toggle('ky-btn-primary', isAct);
      btn.classList.toggle('ky-btn-ghost', !isAct);
      btn.classList.toggle('active', isAct);
    });
    if (listEl) {
      listEl.setAttribute('aria-labelledby', 'mem-tab-' + state.activeTab);
    }
    if (toggleCreateBtn) {
      toggleCreateBtn.setAttribute('aria-expanded', state.showForm && state.activeTab !== 'ledger' ? 'true' : 'false');
      if (state.activeTab === 'ledger') {
        toggleCreateBtn.style.display = 'none';
      } else {
        toggleCreateBtn.style.display = 'inline-flex';
        var labelMap = {
          episodic: state.showForm ? 'Close Form' : '+ Store Episode',
          semantic: state.showForm ? 'Close Form' : '+ Ingest Constraint',
          procedural: state.showForm ? 'Close Form' : '+ New Playbook'
        };
        toggleCreateBtn.textContent = labelMap[state.activeTab] || '+ Add Record';
      }
    }
  }

  function renderIntegrityBanner() {
    if (!bannerEl) return;
    var info = state.integrity;
    if (!info) {
      bannerEl.setAttribute('role', 'status');
      bannerEl.innerHTML = '';
      return;
    }
    var ok = Boolean(info.is_valid !== undefined ? info.is_valid : info.valid);
    bannerEl.setAttribute('role', ok ? 'status' : 'alert');
    var borderCol = ok ? 'rgba(16,185,129,0.35)' : 'rgba(239,68,68,0.48)';
    var bgCol = ok ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.10)';
    var accentCol = ok ? '#34d399' : '#f87171';
    var badgeTxt = ok ? 'SHA-256 HASH CHAIN VERIFIED' : 'CRYPTOGRAPHIC TAMPER DETECTED';
    var detailTxt = ok
      ? 'Monotonic sequence continuity and payload digests verified across ' + Number(info.entry_count || 0) + ' ledger entries.'
      : 'Integrity violation at entry/record ' + esc(info.corrupted_entry_id || 'unknown') + ': ' + esc(info.error_message || 'hash mismatch');

    bannerEl.innerHTML = [
      '<div class="ky-card" style="padding:12px 16px;border:1px solid ' + borderCol + ';background:' + bgCol + ';display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">',
      '  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">',
      '    <span style="font-family:var(--font-mono);font-size:11px;font-weight:700;padding:3px 8px;border-radius:999px;background:rgba(0,0,0,0.25);color:' + accentCol + ';border:1px solid ' + borderCol + '">' + badgeTxt + '</span>',
      '    <span style="font-size:12.5px;color:var(--text-1)">' + detailTxt + '</span>',
      '  </div>',
      '  <div style="font-family:var(--font-mono);font-size:11px;color:var(--text-2);font-variant-numeric:tabular-nums">',
      '    Seq #' + Number(info.latest_sequence || 0) + ' &bull; Head <code style="color:var(--text-1)">' + esc(shortHash(info.head_hash)) + '</code>',
      '  </div>',
      '</div>'
    ].join('\n');
  }

  function renderKPIs() {
    if (!kpiEl) return;
    var epSource = state.allEpisodic.length || (!state.episodicQuery && state.minRetention <= 0.05)
      ? (state.allEpisodic.length ? state.allEpisodic : state.episodic)
      : state.allEpisodic;
    var epCount = epSource.length;
    var avgRet = 0;
    if (epCount > 0) {
      var sumR = 0;
      for (var i = 0; i < epCount; i++) {
        sumR += computeLiveRetention(epSource[i]);
      }
      avgRet = Math.round((sumR / epCount) * 100);
    }

    var semSource = state.allSemantic.length || (state.semanticStatus === 'ALL' && !state.semanticSubject)
      ? (state.allSemantic.length ? state.allSemantic : state.semantic)
      : state.allSemantic;
    var activeSem = 0;
    var conflictSem = 0;
    for (var j = 0; j < semSource.length; j++) {
      var st = String(semSource[j].status || '').toUpperCase();
      if (st === 'ACTIVE') activeSem++;
      if (st === 'CONFLICTING') conflictSem++;
    }

    var pbSource = state.allPlaybooks.length || !state.playbookQuery
      ? (state.allPlaybooks.length ? state.allPlaybooks : state.playbooks)
      : state.allPlaybooks;
    var pbCount = pbSource.length;
    var avgRel = 0;
    if (pbCount > 0) {
      var sumRel = 0;
      for (var k = 0; k < pbCount; k++) {
        sumRel += Number(pbSource[k].reliability !== undefined ? pbSource[k].reliability : 0.5);
      }
      avgRel = Math.round((sumRel / pbCount) * 100);
    }

    var ledgerTotal = state.integrity && state.integrity.entry_count !== undefined
      ? Number(state.integrity.entry_count)
      : state.ledger.length;

    kpiEl.innerHTML = [
      '<div class="ky-card" style="padding:14px 16px">',
      '  <div class="ky-eyebrow" style="color:var(--text-3)">EPISODIC RETENTION</div>',
      '  <div style="font-size:20px;font-weight:700;margin-top:4px;font-family:var(--font-mono);font-variant-numeric:tabular-nums">' + epCount + ' <span style="font-size:12px;font-weight:500;color:var(--text-2)">(' + avgRet + '% avg R(t))</span></div>',
      '</div>',
      '<div class="ky-card" style="padding:14px 16px">',
      '  <div class="ky-eyebrow" style="color:var(--text-3)">SEMANTIC INVARIANTS</div>',
      '  <div style="font-size:20px;font-weight:700;margin-top:4px;font-family:var(--font-mono);font-variant-numeric:tabular-nums">' + activeSem + ' active' + (conflictSem > 0 ? ' <span style="font-size:12px;color:#fbbf24">(' + conflictSem + ' conflict)</span>' : '') + '</div>',
      '</div>',
      '<div class="ky-card" style="padding:14px 16px">',
      '  <div class="ky-eyebrow" style="color:var(--text-3)">PROCEDURAL PLAYBOOKS</div>',
      '  <div style="font-size:20px;font-weight:700;margin-top:4px;font-family:var(--font-mono);font-variant-numeric:tabular-nums">' + pbCount + ' <span style="font-size:12px;font-weight:500;color:var(--text-2)">(' + avgRel + '% Laplace)</span></div>',
      '</div>',
      '<div class="ky-card" style="padding:14px 16px">',
      '  <div class="ky-eyebrow" style="color:var(--text-3)">SIGNED LEDGER BLOCKS</div>',
      '  <div style="font-size:20px;font-weight:700;margin-top:4px;font-family:var(--font-mono);font-variant-numeric:tabular-nums">' + ledgerTotal + '</div>',
      '</div>'
    ].join('\n');
  }

  function renderCreateForm() {
    if (!createPanelEl) return;
    if (!state.showForm || state.activeTab === 'ledger') {
      createPanelEl.style.display = 'none';
      createPanelEl.innerHTML = '';
      return;
    }
    createPanelEl.style.display = 'block';

    if (state.activeTab === 'episodic') {
      createPanelEl.innerHTML = [
        '<form id="mem-form-episodic" class="ky-card" style="padding:18px" novalidate>',
        '  <div style="font-size:13.5px;font-weight:600;margin-bottom:12px">Store New Episodic Experience</div>',
        '  <div style="margin-bottom:12px">',
        '    <label for="mem-ep-content" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px">Experience Narrative *</label>',
        '    <div id="mem-ep-hint" style="font-size:11px;color:var(--text-3);margin-bottom:4px">Signed into the project ledger with Ebbinghaus decay curve R(t) = exp(-t / S).</div>',
        '    <textarea id="mem-ep-content" name="content" rows="2" required aria-describedby="mem-ep-hint" placeholder="e.g. Resolved SQLite connection pool lock contention by enabling WAL mode and busy_timeout=30000" style="width:100%;padding:8px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px"></textarea>',
        '  </div>',
        '  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:12px">',
        '    <div>',
        '      <label for="mem-ep-stability" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px">Initial Stability S (seconds)</label>',
        '      <input id="mem-ep-stability" name="stability" type="number" min="1" step="60" value="86400" required style="width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px;font-family:var(--font-mono)" />',
        '    </div>',
        '    <div>',
        '      <label for="mem-ep-tags" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px">Tags (comma-separated)</label>',
        '      <input id="mem-ep-tags" name="tags" type="text" autocorrect="off" autocapitalize="off" spellcheck="false" placeholder="sqlite, concurrency, fix" style="width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px" />',
        '    </div>',
        '  </div>',
        '  <div style="display:flex;justify-content:flex-end;gap:8px">',
        '    <button type="submit" id="mem-ep-submit" class="ky-btn ky-btn-primary ky-btn-xs" style="padding:6px 14px">Save Episode</button>',
        '  </div>',
        '</form>'
      ].join('\n');

      var epForm = document.getElementById('mem-form-episodic');
      var epSub = document.getElementById('mem-ep-submit');
      if (epForm && epSub) {
        epForm.addEventListener('submit', async function (ev) {
          ev.preventDefault();
          var contentInput = document.getElementById('mem-ep-content');
          var stabInput = document.getElementById('mem-ep-stability');
          var tagsInput = document.getElementById('mem-ep-tags');
          var txt = contentInput ? contentInput.value.trim() : '';
          if (!txt) {
            toast('Please enter episodic memory content.', 'error');
            if (contentInput) contentInput.focus();
            return;
          }
          var rawStab = stabInput ? stabInput.value.trim() : '86400';
          var stab = rawStab === '' ? 86400 : Number(rawStab);
          if (!isFinite(stab) || stab <= 0) {
            toast('Initial stability S must be greater than 0 seconds.', 'error');
            if (stabInput) stabInput.focus();
            return;
          }
          var rawTags = tagsInput ? tagsInput.value : '';
          var tags = rawTags.split(',').map(function (t) { return t.trim(); }).filter(Boolean);
          epSub.disabled = true;
          try {
            await post('/memory/' + encodeURIComponent(state.projectId) + '/episodic', {
              content: txt,
              stability: stab,
              tags: tags
            });
            state.showForm = false;
            syncTabVisuals();
            renderCreateForm();
            toast('Episodic memory stored and signed to SHA-256 ledger.', 'success');
            await loadAllProjectMemory();
          } catch (err) {
            toast(err && err.message ? err.message : 'Failed to store episodic memory', 'error');
            epSub.disabled = false;
          }
        });
      }
      return;
    }

    if (state.activeTab === 'semantic') {
      createPanelEl.innerHTML = [
        '<form id="mem-form-semantic" class="ky-card" style="padding:18px" novalidate>',
        '  <div style="font-size:13.5px;font-weight:600;margin-bottom:12px">Ingest Semantic Architectural Constraint</div>',
        '  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:12px">',
        '    <div>',
        '      <label for="mem-sem-subject" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px">Subject / Domain *</label>',
        '      <input id="mem-sem-subject" name="subject" type="text" required autocorrect="off" autocapitalize="off" spellcheck="false" placeholder="architecture" style="width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px" />',
        '    </div>',
        '    <div>',
        '      <label for="mem-sem-key" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px">Constraint Key *</label>',
        '      <input id="mem-sem-key" name="key" type="text" required autocorrect="off" autocapitalize="off" spellcheck="false" placeholder="primary_database" style="width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px;font-family:var(--font-mono)" />',
        '    </div>',
        '    <div>',
        '      <label for="mem-sem-conf" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px">Confidence [0–1]</label>',
        '      <input id="mem-sem-conf" name="confidence" type="number" min="0" max="1" step="0.05" value="1.0" required style="width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px;font-family:var(--font-mono)" />',
        '    </div>',
        '  </div>',
        '  <div style="margin-bottom:12px">',
        '    <label for="mem-sem-stmt" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px">Declarative Constraint Statement *</label>',
        '    <div id="mem-sem-hint" style="font-size:11px;color:var(--text-3);margin-bottom:4px">Automatically checked for symbolic and 384D vector contradictions against active beliefs.</div>',
        '    <textarea id="mem-sem-stmt" name="statement" rows="2" required aria-describedby="mem-sem-hint" placeholder="e.g. Primary persistence engine must use PostgreSQL 16 with asyncpg" style="width:100%;padding:8px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px"></textarea>',
        '  </div>',
        '  <div style="margin-bottom:12px">',
        '    <label for="mem-sem-deps" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px">Upstream Dependency IDs (optional, comma-separated)</label>',
        '    <input id="mem-sem-deps" name="depends_on_ids" type="text" autocorrect="off" autocapitalize="off" spellcheck="false" placeholder="uuid-1, uuid-2" style="width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:12.5px;font-family:var(--font-mono)" />',
        '  </div>',
        '  <div style="display:flex;justify-content:flex-end;gap:8px">',
        '    <button type="submit" id="mem-sem-submit" class="ky-btn ky-btn-primary ky-btn-xs" style="padding:6px 14px">Ingest Constraint</button>',
        '  </div>',
        '</form>'
      ].join('\n');

      var semForm = document.getElementById('mem-form-semantic');
      var semSub = document.getElementById('mem-sem-submit');
      if (semForm && semSub) {
        semForm.addEventListener('submit', async function (ev) {
          ev.preventDefault();
          var subEl = document.getElementById('mem-sem-subject');
          var keyEl = document.getElementById('mem-sem-key');
          var stmtEl = document.getElementById('mem-sem-stmt');
          var confEl = document.getElementById('mem-sem-conf');
          var depsEl = document.getElementById('mem-sem-deps');
          var subject = subEl ? subEl.value.trim() : '';
          var key = keyEl ? keyEl.value.trim() : '';
          var statement = stmtEl ? stmtEl.value.trim() : '';
          if (!subject || !key || !statement) {
            toast('Subject, key, and statement are required.', 'error');
            if (!subject && subEl) subEl.focus();
            else if (!key && keyEl) keyEl.focus();
            else if (stmtEl) stmtEl.focus();
            return;
          }
          var rawConf = confEl ? confEl.value.trim() : '1.0';
          var conf = rawConf === '' ? 1.0 : Number(rawConf);
          if (!isFinite(conf) || conf < 0 || conf > 1) {
            toast('Confidence must be between 0.0 and 1.0.', 'error');
            if (confEl) confEl.focus();
            return;
          }
          var deps = (depsEl ? depsEl.value : '').split(',').map(function (d) { return d.trim(); }).filter(Boolean);
          semSub.disabled = true;
          try {
            var res = await post('/memory/' + encodeURIComponent(state.projectId) + '/semantic', {
              subject: subject,
              key: key,
              statement: statement,
              confidence: conf,
              depends_on_ids: deps
            });
            state.showForm = false;
            syncTabVisuals();
            renderCreateForm();
            if (res && res.conflict && res.conflict.has_conflict) {
              toast('Contradiction detected (' + res.conflict.action_taken + '): ' + res.conflict.reason, 'info');
            } else {
              toast('Semantic constraint stored as ACTIVE.', 'success');
            }
            await loadAllProjectMemory();
          } catch (err) {
            toast(err && err.message ? err.message : 'Failed to store semantic constraint', 'error');
            semSub.disabled = false;
          }
        });
      }
      return;
    }

    if (state.activeTab === 'procedural') {
      createPanelEl.innerHTML = [
        '<form id="mem-form-playbook" class="ky-card" style="padding:18px" novalidate>',
        '  <div style="font-size:13.5px;font-weight:600;margin-bottom:12px">Codify Procedural Execution Playbook</div>',
        '  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-bottom:12px">',
        '    <div>',
        '      <label for="mem-pb-name" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px">Playbook Name *</label>',
        '      <input id="mem-pb-name" name="name" type="text" required placeholder="Zero-Downtime Alembic Migration" style="width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px" />',
        '    </div>',
        '    <div>',
        '      <label for="mem-pb-tags" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px">Trigger Tags (comma-separated)</label>',
        '      <input id="mem-pb-tags" name="trigger_tags" type="text" autocorrect="off" autocapitalize="off" spellcheck="false" placeholder="database, migration, ci" style="width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px" />',
        '    </div>',
        '  </div>',
        '  <div style="margin-bottom:12px">',
        '    <label for="mem-pb-desc" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px">Description</label>',
        '    <input id="mem-pb-desc" name="description" type="text" placeholder="Validates schema revisions in cleanroom sandbox before rollout" style="width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px" />',
        '  </div>',
        '  <div style="margin-bottom:12px">',
        '    <label for="mem-pb-steps" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px">Ordered Execution Steps (one step per line) *</label>',
        '    <textarea id="mem-pb-steps" name="steps" rows="3" required placeholder="pytest tests/test_migrations.py\nalembic upgrade head\npython -m scripts.verify_health" style="width:100%;padding:8px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:12.5px;font-family:var(--font-mono)"></textarea>',
        '  </div>',
        '  <div style="margin-bottom:12px">',
        '    <label for="mem-pb-pre" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px">Preconditions (comma-separated)</label>',
        '    <input id="mem-pb-pre" name="preconditions" type="text" placeholder="clean git worktree, backup snapshot verified" style="width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px" />',
        '  </div>',
        '  <div style="display:flex;justify-content:flex-end;gap:8px">',
        '    <button type="submit" id="mem-pb-submit" class="ky-btn ky-btn-primary ky-btn-xs" style="padding:6px 14px">Create Playbook</button>',
        '  </div>',
        '</form>'
      ].join('\n');

      var pbForm = document.getElementById('mem-form-playbook');
      var pbSub = document.getElementById('mem-pb-submit');
      if (pbForm && pbSub) {
        pbForm.addEventListener('submit', async function (ev) {
          ev.preventDefault();
          var nameEl = document.getElementById('mem-pb-name');
          var descEl = document.getElementById('mem-pb-desc');
          var stepsEl = document.getElementById('mem-pb-steps');
          var tagsEl = document.getElementById('mem-pb-tags');
          var preEl = document.getElementById('mem-pb-pre');
          var name = nameEl ? nameEl.value.trim() : '';
          var rawSteps = stepsEl ? stepsEl.value : '';
          var steps = rawSteps.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
          if (!name || !steps.length) {
            toast('Playbook name and at least one step are required.', 'error');
            if (!name && nameEl) nameEl.focus();
            else if (stepsEl) stepsEl.focus();
            return;
          }
          var tags = (tagsEl ? tagsEl.value : '').split(',').map(function (t) { return t.trim(); }).filter(Boolean);
          var pre = (preEl ? preEl.value : '').split(',').map(function (p) { return p.trim(); }).filter(Boolean);
          pbSub.disabled = true;
          try {
            await post('/memory/' + encodeURIComponent(state.projectId) + '/playbooks', {
              name: name,
              description: descEl ? descEl.value.trim() : '',
              steps: steps,
              trigger_tags: tags,
              preconditions: pre
            });
            state.showForm = false;
            syncTabVisuals();
            renderCreateForm();
            toast('Procedural playbook created with Laplace prior 0.50.', 'success');
            await loadAllProjectMemory();
          } catch (err) {
            toast(err && err.message ? err.message : 'Failed to create playbook', 'error');
            pbSub.disabled = false;
          }
        });
      }
    }
  }

  function renderFilterBar() {
    if (!filterBarEl) return;
    if (state.activeTab === 'episodic') {
      var pct = Math.round(state.minRetention * 100);
      filterBarEl.innerHTML = [
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">',
        '  <div style="display:flex;align-items:center;gap:10px;flex:1;min-width:220px">',
        '    <input id="mem-ep-search" type="search" aria-label="Search episodic memories" value="' + esc(state.episodicQuery) + '" placeholder="Search episodic experiences by keyword or concept…" style="flex:1;max-width:380px;padding:7px 11px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:12.5px" />',
        '    <button type="button" id="mem-ep-search-btn" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border)">Search</button>',
        '  </div>',
        '  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">',
        '    <label for="mem-ep-min-ret" style="font-size:12px;color:var(--text-2);display:inline-flex;align-items:center;gap:8px">',
        '      <span>Min Retention R(t): <strong id="mem-ep-ret-val" style="font-family:var(--font-mono)">' + pct + '%</strong></span>',
        '      <input id="mem-ep-min-ret" type="range" min="0" max="95" step="5" value="' + pct + '" style="width:110px;cursor:pointer" />',
        '    </label>',
        '    <button type="button" id="mem-ep-prune-btn" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border);font-size:11.5px" title="Prune episodes decayed below 5% retention">Prune Decayed</button>',
        '  </div>',
        '</div>'
      ].join('\n');

      var searchBtn = document.getElementById('mem-ep-search-btn');
      var searchInp = document.getElementById('mem-ep-search');
      var rangeInp = document.getElementById('mem-ep-min-ret');
      var pruneBtn = document.getElementById('mem-ep-prune-btn');

      if (searchBtn && searchInp) {
        var triggerEpSearch = async function () {
          state.episodicQuery = searchInp.value.trim();
          await loadEpisodicOnly();
        };
        searchBtn.addEventListener('click', triggerEpSearch);
        searchInp.addEventListener('keydown', function (ev) {
          if (ev.key === 'Enter' && !ev.isComposing) {
            ev.preventDefault();
            triggerEpSearch();
          }
        });
      }
      if (rangeInp) {
        rangeInp.addEventListener('change', async function (ev) {
          state.minRetention = Number(ev.target.value || 0) / 100.0;
          var lbl = document.getElementById('mem-ep-ret-val');
          if (lbl) lbl.textContent = Math.round(state.minRetention * 100) + '%';
          await loadEpisodicOnly();
        });
      }
      if (pruneBtn) {
        pruneBtn.addEventListener('click', async function () {
          try {
            var res = await post('/memory/' + encodeURIComponent(state.projectId) + '/episodic/prune', { min_retention: 0.05 });
            toast('Pruned ' + Number((res && res.pruned_count) || 0) + ' decayed episodic memories.', 'info');
            await loadAllProjectMemory();
          } catch (err) {
            toast(err && err.message ? err.message : 'Prune failed', 'error');
          }
        });
      }
      return;
    }

    if (state.activeTab === 'semantic') {
      var statuses = ['ALL', 'ACTIVE', 'CONFLICTING', 'SUPERSEDED'];
      var pillsHtml = statuses.map(function (st) {
        var active = state.semanticStatus === st;
        return '<button type="button" aria-pressed="' + (active ? 'true' : 'false') + '" class="mem-sem-status-filter ky-btn ' + (active ? 'ky-btn-primary' : 'ky-btn-ghost') + ' ky-btn-xs" data-status="' + st + '" style="font-size:11.5px">' + st + '</button>';
      }).join('');

      filterBarEl.innerHTML = [
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">',
        '  <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">' + pillsHtml + '</div>',
        '  <div style="display:flex;align-items:center;gap:8px">',
        '    <input id="mem-sem-subj-filter" type="search" autocorrect="off" autocapitalize="off" spellcheck="false" aria-label="Filter semantic constraints by subject" value="' + esc(state.semanticSubject) + '" placeholder="Filter by subject…" style="padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:12px" />',
        '    <button type="button" id="mem-sem-subj-btn" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border)">Filter</button>',
        '  </div>',
        '</div>'
      ].join('\n');

      var statusBtns = Array.prototype.slice.call(filterBarEl.querySelectorAll('.mem-sem-status-filter'));
      statusBtns.forEach(function (b) {
        b.addEventListener('click', async function () {
          state.semanticStatus = b.getAttribute('data-status') || 'ALL';
          renderFilterBar();
          await loadSemanticOnly();
        });
      });

      var subjBtn = document.getElementById('mem-sem-subj-btn');
      var subjInp = document.getElementById('mem-sem-subj-filter');
      if (subjBtn && subjInp) {
        var triggerSemFilter = async function () {
          state.semanticSubject = subjInp.value.trim();
          await loadSemanticOnly();
        };
        subjBtn.addEventListener('click', triggerSemFilter);
        subjInp.addEventListener('keydown', function (ev) {
          if (ev.key === 'Enter' && !ev.isComposing) {
            ev.preventDefault();
            triggerSemFilter();
          }
        });
      }
      return;
    }

    if (state.activeTab === 'procedural') {
      filterBarEl.innerHTML = [
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">',
        '  <input id="mem-pb-search" type="search" autocorrect="off" autocapitalize="off" spellcheck="false" aria-label="Search procedural playbooks" value="' + esc(state.playbookQuery) + '" placeholder="Match playbooks by task context or trigger tag…" style="flex:1;max-width:420px;padding:7px 11px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:12.5px" />',
        '  <button type="button" id="mem-pb-search-btn" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border)">Rank Playbooks</button>',
        '</div>'
      ].join('\n');

      var pbSearchBtn = document.getElementById('mem-pb-search-btn');
      var pbSearchInp = document.getElementById('mem-pb-search');
      if (pbSearchBtn && pbSearchInp) {
        var triggerPbSearch = async function () {
          state.playbookQuery = pbSearchInp.value.trim();
          await loadPlaybooksOnly();
        };
        pbSearchBtn.addEventListener('click', triggerPbSearch);
        pbSearchInp.addEventListener('keydown', function (ev) {
          if (ev.key === 'Enter' && !ev.isComposing) {
            ev.preventDefault();
            triggerPbSearch();
          }
        });
      }
      return;
    }

    filterBarEl.innerHTML = '';
  }

  function renderActiveTabContent() {
    if (!listEl) return;

    if (state.activeTab === 'episodic') {
      if (!state.episodic.length) {
        listEl.innerHTML = emptyState(
          'No episodic memories above retention threshold',
          'Store debugging episodes or lower the Ebbinghaus R(t) threshold slider to inspect decayed experiences.'
        );
        return;
      }

      listEl.innerHTML = state.episodic.map(function (item) {
        var r = computeLiveRetention(item);
        var rPct = Math.round(r * 1000) / 10;
        var barColor = retentionColor(r);
        var tags = Array.isArray(item.tags) ? item.tags : [];
        var tagPills = tags.map(function (t) {
          return '<span style="font-family:var(--font-mono);font-size:11px;padding:2px 7px;border-radius:999px;background:var(--surface-2);border:1px solid var(--border);color:var(--text-2)">#' + esc(t) + '</span>';
        }).join(' ');

        return [
          '<div class="ky-card" style="padding:16px 18px;margin-bottom:10px">',
          '  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap">',
          '    <div style="flex:1;min-width:240px">',
          '      <div style="font-size:13.5px;color:var(--text-1);line-height:1.55;margin-bottom:8px">' + esc(item.content) + '</div>',
          '      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:11.5px;color:var(--text-3)">',
          '        <span style="font-family:var(--font-mono);font-variant-numeric:tabular-nums">Stability S = <strong style="color:var(--text-2)">' + esc(formatDurationSec(item.stability)) + '</strong></span>',
          '        <span>&bull;</span>',
          '        <span style="font-family:var(--font-mono);font-variant-numeric:tabular-nums">Recalls: <strong style="color:var(--text-2)">' + Number(item.recall_count || 0) + '</strong></span>',
          '        <span>&bull;</span>',
          '        <span>Last recalled ' + esc(timeAgo(item.last_recalled_at || item.created_at)) + '</span>',
          '        ' + tagPills,
          '      </div>',
          '    </div>',
          '    <div style="min-width:190px;display:flex;flex-direction:column;align-items:flex-end;gap:8px">',
          '      <div style="width:100%">',
          '        <div style="display:flex;justify-content:space-between;font-family:var(--font-mono);font-size:11px;margin-bottom:4px;font-variant-numeric:tabular-nums">',
          '          <span style="color:var(--text-3)">Retention R(t)</span>',
          '          <strong style="color:' + barColor + '">' + rPct.toFixed(1) + '%</strong>',
          '        </div>',
          '        <div style="width:100%;height:6px;background:var(--surface-2);border-radius:999px;overflow:hidden">',
          '          <div style="width:' + Math.max(2, Math.min(100, rPct)) + '%;height:100%;background:' + barColor + ';border-radius:999px"></div>',
          '        </div>',
          '      </div>',
          '      <button type="button" class="mem-recall-btn ky-btn ky-btn-ghost ky-btn-xs" data-id="' + esc(item.id) + '" style="border:1px solid var(--border);font-size:11.5px">&#x26A1; Recall / Reinforce (+50% S)</button>',
          '    </div>',
          '  </div>',
          '</div>'
        ].join('\n');
      }).join('\n');

      var recallBtns = Array.prototype.slice.call(listEl.querySelectorAll('.mem-recall-btn'));
      recallBtns.forEach(function (btn) {
        btn.addEventListener('click', async function () {
          var memId = btn.getAttribute('data-id');
          if (!memId) return;
          btn.disabled = true;
          try {
            var updated = await post(
              '/memory/' + encodeURIComponent(state.projectId) + '/episodic/' + encodeURIComponent(memId) + '/recall',
              { alpha: 0.5 }
            );
            toast('Reinforced! Stability increased to ' + formatDurationSec(updated && updated.stability) + ' and R(t) reset to 100%.', 'success');
            await loadAllProjectMemory();
          } catch (err) {
            toast(err && err.message ? err.message : 'Recall failed', 'error');
            btn.disabled = false;
          }
        });
      });
      return;
    }

    if (state.activeTab === 'semantic') {
      if (!state.semantic.length) {
        listEl.innerHTML = emptyState(
          'No semantic constraints matching filter',
          'Ingest architectural invariants or switch status filter to ALL to inspect superseded and conflicting beliefs.'
        );
        return;
      }

      listEl.innerHTML = state.semantic.map(function (item) {
        var st = String(item.status || 'ACTIVE').toUpperCase();
        var confPct = Math.round(Number(item.confidence || 0) * 100);
        var basePct = Math.round(Number(item.base_confidence !== undefined ? item.base_confidence : item.confidence || 0) * 100);
        var deps = Array.isArray(item.depends_on_ids) ? item.depends_on_ids : [];
        var attenuated = basePct > confPct;

        var lineageHtml = [];
        if (item.superseded_by_id) {
          lineageHtml.push('<span style="font-family:var(--font-mono);font-size:11px;color:#94a3b8">Superseded by: <code>' + esc(shortHash(item.superseded_by_id)) + '</code></span>');
        }
        if (item.conflicted_with_id) {
          lineageHtml.push('<span style="font-family:var(--font-mono);font-size:11px;color:#fbbf24">Collision ref: <code>' + esc(shortHash(item.conflicted_with_id)) + '</code></span>');
        }
        if (deps.length > 0) {
          lineageHtml.push('<span style="font-family:var(--font-mono);font-size:11px;color:var(--text-3)">Depends on: ' + deps.map(function (d) { return '<code>' + esc(shortHash(d)) + '</code>'; }).join(', ') + '</span>');
        }

        var resolveBtnHtml = '';
        if (st === 'CONFLICTING' && item.conflicted_with_id) {
          var labelCtx = esc((item.subject || '') + ' :: ' + (item.key || ''));
          resolveBtnHtml = [
            '<button type="button" class="mem-sem-resolve-btn ky-btn ky-btn-primary ky-btn-xs" aria-label="Promote &amp; Supersede Prior for ' + labelCtx + '" data-win="' + esc(item.id) + '" data-sup="' + esc(item.conflicted_with_id) + '" style="font-size:11px">Promote &amp; Supersede Prior</button>',
            '<button type="button" class="mem-sem-resolve-btn ky-btn ky-btn-ghost ky-btn-xs" aria-label="Keep Prior &amp; Supersede This for ' + labelCtx + '" data-win="' + esc(item.conflicted_with_id) + '" data-sup="' + esc(item.id) + '" style="border:1px solid var(--border);font-size:11px">Keep Prior &amp; Supersede This</button>'
          ].join(' ');
        }

        return [
          '<div class="ky-card" style="padding:16px 18px;margin-bottom:10px">',
          '  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">',
          '    <div style="flex:1;min-width:240px">',
          '      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px">',
          '        <span style="font-family:var(--font-mono);font-size:10.5px;font-weight:700;padding:2px 8px;border-radius:999px;' + statusBadgeStyle(st) + '">' + esc(st) + '</span>',
          '        <span style="font-family:var(--font-mono);font-size:12.5px;font-weight:600;color:var(--text-1)">' + esc(item.subject) + ' :: ' + esc(item.key) + '</span>',
          '        <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-3)">ID ' + esc(shortHash(item.id)) + '</span>',
          '      </div>',
          '      <div style="font-size:13.5px;color:var(--text-1);line-height:1.5;margin-bottom:6px">' + esc(item.statement) + '</div>',
          (item.conflict_reason ? '<div style="font-size:11.5px;color:#fbbf24;margin-bottom:6px">Provenance: ' + esc(item.conflict_reason) + '</div>' : ''),
          (lineageHtml.length ? '<div style="display:flex;gap:12px;flex-wrap:wrap">' + lineageHtml.join('') + '</div>' : ''),
          '    </div>',
          '    <div style="display:flex;flex-direction:column;align-items:flex-end;gap:8px">',
          '      <div style="font-family:var(--font-mono);font-size:11.5px;font-variant-numeric:tabular-nums;color:var(--text-2)">',
          '        C<sub>eff</sub> = <strong>' + confPct + '%</strong>' + (attenuated ? ' <span style="color:#f59e0b">(attenuated from ' + basePct + '%)</span>' : ''),
          '      </div>',
          '      <div style="display:flex;gap:6px;flex-wrap:wrap">',
          '        ' + resolveBtnHtml,
          '        <button type="button" class="mem-sem-prop-btn ky-btn ky-btn-ghost ky-btn-xs" data-id="' + esc(item.id) + '" style="border:1px solid var(--border);font-size:11px" title="Run cycle-safe downstream belief propagation">&#x21BB; Propagate</button>',
          '      </div>',
          '    </div>',
          '  </div>',
          '</div>'
        ].join('\n');
      }).join('\n');

      var resolveBtns = Array.prototype.slice.call(listEl.querySelectorAll('.mem-sem-resolve-btn'));
      resolveBtns.forEach(function (btn) {
        btn.addEventListener('click', async function () {
          var winId = btn.getAttribute('data-win');
          var supId = btn.getAttribute('data-sup');
          if (!winId || !supId) return;
          btn.disabled = true;
          try {
            await post('/memory/' + encodeURIComponent(state.projectId) + '/semantic/resolve', {
              winning_id: winId,
              superseded_id: supId,
              reason: 'Resolved from Memory OS Inspector UI'
            });
            toast('Conflict resolved and downstream beliefs attenuated.', 'success');
            await loadAllProjectMemory();
          } catch (err) {
            toast(err && err.message ? err.message : 'Conflict resolution failed', 'error');
            btn.disabled = false;
          }
        });
      });

      var propBtns = Array.prototype.slice.call(listEl.querySelectorAll('.mem-sem-prop-btn'));
      propBtns.forEach(function (btn) {
        btn.addEventListener('click', async function () {
          var rootId = btn.getAttribute('data-id');
          if (!rootId) return;
          btn.disabled = true;
          try {
            var rep = await post('/memory/' + encodeURIComponent(state.projectId) + '/semantic/' + encodeURIComponent(rootId) + '/propagate', {});
            toast('Belief propagation converged in ' + Number((rep && rep.iterations) || 0) + ' iteration(s); ' + Number((rep && rep.affected_count) || 0) + ' dependent(s) updated.', 'info');
            await loadAllProjectMemory();
          } catch (err) {
            toast(err && err.message ? err.message : 'Propagation failed', 'error');
            btn.disabled = false;
          }
        });
      });
      return;
    }

    if (state.activeTab === 'procedural') {
      if (!state.playbooks.length) {
        listEl.innerHTML = emptyState(
          'No procedural playbooks codified yet',
          'Create reusable multi-step execution playbooks with empirical Laplace reliability tracking.'
        );
        return;
      }

      listEl.innerHTML = state.playbooks.map(function (pb) {
        var rel = Number(pb.reliability !== undefined ? pb.reliability : 0.5);
        var relPct = Math.round(rel * 1000) / 10;
        var relCol = retentionColor(rel);
        var steps = Array.isArray(pb.steps) ? pb.steps : [];
        var tags = Array.isArray(pb.trigger_tags) ? pb.trigger_tags : [];
        var preconds = Array.isArray(pb.preconditions) ? pb.preconditions : [];

        var stepsHtml = steps.map(function (st) {
          var label = typeof st === 'object' && st !== null
            ? (st.command || st.action || st.description || JSON.stringify(st))
            : String(st);
          return '<li style="margin-bottom:4px;font-family:var(--font-mono);font-size:12px;color:var(--text-1)">' + esc(label) + '</li>';
        }).join('');

        var tagHtml = tags.map(function (t) {
          return '<span style="font-family:var(--font-mono);font-size:11px;padding:2px 7px;border-radius:999px;background:var(--surface-2);border:1px solid var(--border);color:var(--text-2)">#' + esc(t) + '</span>';
        }).join(' ');

        return [
          '<div class="ky-card" style="padding:16px 18px;margin-bottom:10px">',
          '  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap">',
          '    <div style="flex:1;min-width:250px">',
          '      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">',
          '        <strong style="font-size:14.5px;color:var(--text-1)">' + esc(pb.name) + '</strong>',
          '        <span style="font-family:var(--font-mono);font-size:11px;padding:1px 7px;border-radius:6px;background:var(--surface-2);border:1px solid var(--border)">v' + Number(pb.version || 1) + '</span>',
          '        ' + tagHtml,
          '      </div>',
          (pb.description ? '<div style="font-size:12.5px;color:var(--text-2);margin-bottom:8px">' + esc(pb.description) + '</div>' : ''),
          (preconds.length ? '<div style="font-size:11.5px;color:var(--text-3);margin-bottom:8px">Preconditions: ' + preconds.map(function (p) { return esc(p); }).join(' &bull; ') + '</div>' : ''),
          (stepsHtml ? '<ol style="margin:6px 0 0 18px;padding:0">' + stepsHtml + '</ol>' : ''),
          '    </div>',
          '    <div style="min-width:210px;display:flex;flex-direction:column;align-items:flex-end;gap:8px">',
          '      <div style="width:100%">',
          '        <div style="display:flex;justify-content:space-between;font-family:var(--font-mono);font-size:11px;margin-bottom:4px;font-variant-numeric:tabular-nums">',
          '          <span style="color:var(--text-3)">Laplace Reliability</span>',
          '          <strong style="color:' + relCol + '">' + relPct.toFixed(1) + '%</strong>',
          '        </div>',
          '        <div style="width:100%;height:6px;background:var(--surface-2);border-radius:999px;overflow:hidden">',
          '          <div style="width:' + Math.max(2, Math.min(100, relPct)) + '%;height:100%;background:' + relCol + ';border-radius:999px"></div>',
          '        </div>',
          '        <div style="font-family:var(--font-mono);font-size:11px;color:var(--text-3);margin-top:4px;text-align:right;font-variant-numeric:tabular-nums">',
          '          &#x2713; ' + Number(pb.success_count || 0) + ' ok &bull; &#x2715; ' + Number(pb.failure_count || 0) + ' fail',
          '        </div>',
          '      </div>',
          '      <div style="display:flex;gap:6px">',
          '        <button type="button" class="mem-pb-exec-btn ky-btn ky-btn-ghost ky-btn-xs" aria-label="+ SUCCESS for ' + esc(pb.name) + '" data-id="' + esc(pb.id) + '" data-outcome="SUCCESS" style="border:1px solid rgba(16,185,129,0.35);color:#34d399;font-size:11px">+ SUCCESS</button>',
          '        <button type="button" class="mem-pb-exec-btn ky-btn ky-btn-ghost ky-btn-xs" aria-label="+ FAILURE for ' + esc(pb.name) + '" data-id="' + esc(pb.id) + '" data-outcome="FAILURE" style="border:1px solid rgba(239,68,68,0.35);color:#f87171;font-size:11px">+ FAILURE</button>',
          '      </div>',
          '    </div>',
          '  </div>',
          '</div>'
        ].join('\n');
      }).join('\n');

      var execBtns = Array.prototype.slice.call(listEl.querySelectorAll('.mem-pb-exec-btn'));
      execBtns.forEach(function (btn) {
        btn.addEventListener('click', async function () {
          var pbId = btn.getAttribute('data-id');
          var outcome = btn.getAttribute('data-outcome');
          if (!pbId || !outcome) return;
          btn.disabled = true;
          try {
            var updatedPb = await post(
              '/memory/' + encodeURIComponent(state.projectId) + '/playbooks/' + encodeURIComponent(pbId) + '/execute',
              { outcome: outcome }
            );
            toast('Recorded ' + outcome + ' — Laplace reliability now ' + Math.round(Number((updatedPb && updatedPb.reliability) || 0.5) * 100) + '%.', 'success');
            await loadAllProjectMemory();
          } catch (err) {
            toast(err && err.message ? err.message : 'Telemetry update failed', 'error');
            btn.disabled = false;
          }
        });
      });
      return;
    }

    // Ledger Audit Trail Tab
    if (!state.ledger.length) {
      listEl.innerHTML = emptyState(
        'No cryptographic ledger mutations recorded yet',
        'Every episodic store/recall, semantic belief update, and playbook execution appends a SHA-256 hash-chained block.'
      );
      return;
    }

    var rowsHtml = state.ledger.map(function (entry) {
      return [
        '<tr style="border-bottom:1px solid var(--border)">',
        '  <td style="padding:10px 12px;font-family:var(--font-mono);font-size:12px;font-variant-numeric:tabular-nums">#' + Number(entry.sequence_number || 0) + '</td>',
        '  <td style="padding:10px 12px;font-family:var(--font-mono);font-size:11.5px;color:var(--text-1)">' + esc(entry.mutation_type) + '</td>',
        '  <td style="padding:10px 12px;font-family:var(--font-mono);font-size:11.5px;color:var(--text-2)" title="' + esc(entry.record_id) + '">' + esc(shortHash(entry.record_id)) + '</td>',
        '  <td style="padding:10px 12px;font-family:var(--font-mono);font-size:11.5px;color:var(--text-3)" title="' + esc(entry.previous_hash) + '">' + esc(shortHash(entry.previous_hash)) + '</td>',
        '  <td style="padding:10px 12px;font-family:var(--font-mono);font-size:11.5px;color:#34d399" title="' + esc(entry.entry_hash) + '">' + esc(shortHash(entry.entry_hash)) + '</td>',
        '  <td style="padding:10px 12px;font-size:11.5px;color:var(--text-3);white-space:nowrap">' + esc(timeAgo(entry.timestamp || entry.created_at)) + '</td>',
        '</tr>'
      ].join('\n');
    }).join('\n');

    listEl.innerHTML = [
      '<div class="ky-card" style="padding:0;overflow:hidden">',
      '  <div style="overflow-x:auto;-webkit-overflow-scrolling:touch">',
      '    <table style="width:100%;border-collapse:collapse;text-align:left">',
      '      <caption style="position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;border:0">SHA-256 Cryptographic Mutation Audit Ledger</caption>',
      '      <thead>',
      '        <tr style="border-bottom:1px solid var(--border);background:var(--surface-2);font-family:var(--font-mono);font-size:11px;color:var(--text-3);text-transform:uppercase">',
      '          <th scope="col" style="padding:10px 12px">Seq</th>',
      '          <th scope="col" style="padding:10px 12px">Mutation</th>',
      '          <th scope="col" style="padding:10px 12px">Record ID</th>',
      '          <th scope="col" style="padding:10px 12px">Prev Hash</th>',
      '          <th scope="col" style="padding:10px 12px">Entry SHA-256</th>',
      '          <th scope="col" style="padding:10px 12px">Time</th>',
      '        </tr>',
      '      </thead>',
      '      <tbody>' + rowsHtml + '</tbody>',
      '    </table>',
      '  </div>',
      '</div>'
    ].join('\n');
  }

  async function loadEpisodicOnly() {
    if (!state.projectId) return;
    try {
      var q = '?min_retention=' + encodeURIComponent(state.minRetention) + '&limit=50';
      if (state.episodicQuery) {
        q += '&query=' + encodeURIComponent(state.episodicQuery);
      }
      var epRes = await get('/memory/' + encodeURIComponent(state.projectId) + '/episodic' + q);
      state.episodic = (epRes && Array.isArray(epRes.results)) ? epRes.results : [];
      if (!state.episodicQuery && state.minRetention <= 0.05) {
        state.allEpisodic = state.episodic.slice();
      }
      renderKPIs();
      renderActiveTabContent();
    } catch (err) {
      toast(err && err.message ? err.message : 'Failed to filter episodic memories', 'error');
    }
  }

  async function loadSemanticOnly() {
    if (!state.projectId) return;
    try {
      var q = '?status=' + encodeURIComponent(state.semanticStatus) + '&limit=50';
      if (state.semanticSubject) {
        q += '&subject=' + encodeURIComponent(state.semanticSubject);
      }
      var semRes = await get('/memory/' + encodeURIComponent(state.projectId) + '/semantic' + q);
      state.semantic = (semRes && Array.isArray(semRes.results)) ? semRes.results : [];
      if (state.semanticStatus === 'ALL' && !state.semanticSubject) {
        state.allSemantic = state.semantic.slice();
      }
      renderKPIs();
      renderActiveTabContent();
    } catch (err) {
      toast(err && err.message ? err.message : 'Failed to filter semantic constraints', 'error');
    }
  }

  async function loadPlaybooksOnly() {
    if (!state.projectId) return;
    try {
      var q = '?limit=30';
      if (state.playbookQuery) {
        q += '&query=' + encodeURIComponent(state.playbookQuery);
      }
      var pbRes = await get('/memory/' + encodeURIComponent(state.projectId) + '/playbooks' + q);
      state.playbooks = (pbRes && Array.isArray(pbRes.results)) ? pbRes.results : [];
      if (!state.playbookQuery) {
        state.allPlaybooks = state.playbooks.slice();
      }
      renderKPIs();
      renderActiveTabContent();
    } catch (err) {
      toast(err && err.message ? err.message : 'Failed to search procedural playbooks', 'error');
    }
  }

  async function loadAllProjectMemory() {
    if (!state.projectId) return;
    try {
      var pidEnc = encodeURIComponent(state.projectId);
      var epFiltered = Boolean(state.episodicQuery || state.minRetention > 0.05);
      var semFiltered = Boolean(state.semanticStatus !== 'ALL' || state.semanticSubject);
      var pbFiltered = Boolean(state.playbookQuery);

      var epUrl = '/memory/' + pidEnc + '/episodic?min_retention=' + encodeURIComponent(state.minRetention) + '&limit=50' + (state.episodicQuery ? '&query=' + encodeURIComponent(state.episodicQuery) : '');
      var semUrl = '/memory/' + pidEnc + '/semantic?status=' + encodeURIComponent(state.semanticStatus) + '&limit=50' + (state.semanticSubject ? '&subject=' + encodeURIComponent(state.semanticSubject) : '');
      var pbUrl = '/memory/' + pidEnc + '/playbooks?limit=30' + (state.playbookQuery ? '&query=' + encodeURIComponent(state.playbookQuery) : '');
      var verifyUrl = '/memory/' + pidEnc + '/ledger/verify';
      var ledgerUrl = '/memory/' + pidEnc + '/ledger?limit=50';

      var results = await Promise.all([
        get(epUrl).catch(function () { return { results: [] }; }),
        get(semUrl).catch(function () { return { results: [] }; }),
        get(pbUrl).catch(function () { return { results: [] }; }),
        get(verifyUrl).catch(function () { return null; }),
        get(ledgerUrl).catch(function () { return { entries: [] }; }),
        epFiltered ? get('/memory/' + pidEnc + '/episodic?min_retention=0.05&limit=50').catch(function () { return null; }) : Promise.resolve(null),
        semFiltered ? get('/memory/' + pidEnc + '/semantic?status=ALL&limit=50').catch(function () { return null; }) : Promise.resolve(null),
        pbFiltered ? get('/memory/' + pidEnc + '/playbooks?limit=30').catch(function () { return null; }) : Promise.resolve(null)
      ]);

      state.episodic = (results[0] && Array.isArray(results[0].results)) ? results[0].results : [];
      state.semantic = (results[1] && Array.isArray(results[1].results)) ? results[1].results : [];
      state.playbooks = (results[2] && Array.isArray(results[2].results)) ? results[2].results : [];
      state.integrity = results[3];
      state.ledger = (results[4] && Array.isArray(results[4].entries)) ? results[4].entries : [];

      state.allEpisodic = epFiltered && results[5] && Array.isArray(results[5].results) ? results[5].results : state.episodic.slice();
      state.allSemantic = semFiltered && results[6] && Array.isArray(results[6].results) ? results[6].results : state.semantic.slice();
      state.allPlaybooks = pbFiltered && results[7] && Array.isArray(results[7].results) ? results[7].results : state.playbooks.slice();

      renderIntegrityBanner();
      renderKPIs();
      renderFilterBar();
      renderActiveTabContent();
    } catch (err) {
      if (listEl) listEl.innerHTML = errorBox(err);
    }
  }

  tabButtons.forEach(function (btn, idx) {
    btn.addEventListener('click', function () {
      state.activeTab = btn.getAttribute('data-tab') || 'episodic';
      state.showForm = false;
      syncTabVisuals();
      renderCreateForm();
      renderFilterBar();
      renderActiveTabContent();
    });
    btn.addEventListener('keydown', function (ev) {
      var nextIdx = -1;
      if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') {
        var delta = ev.key === 'ArrowRight' ? 1 : -1;
        nextIdx = (idx + delta + tabButtons.length) % tabButtons.length;
      } else if (ev.key === 'Home') {
        nextIdx = 0;
      } else if (ev.key === 'End') {
        nextIdx = tabButtons.length - 1;
      }
      if (nextIdx >= 0) {
        ev.preventDefault();
        tabButtons[nextIdx].focus();
        tabButtons[nextIdx].click();
      }
    });
  });

  if (toggleCreateBtn) {
    toggleCreateBtn.addEventListener('click', function () {
      state.showForm = !state.showForm;
      syncTabVisuals();
      renderCreateForm();
    });
  }

  if (verifyBtn) {
    verifyBtn.addEventListener('click', async function () {
      if (!state.projectId) return;
      verifyBtn.disabled = true;
      try {
        var ver = await get('/memory/' + encodeURIComponent(state.projectId) + '/ledger/verify');
        state.integrity = ver;
        renderIntegrityBanner();
        renderKPIs();
        if (ver && (ver.is_valid || ver.valid)) {
          toast('SHA-256 Ledger Integrity Verified (' + Number(ver.entry_count || 0) + ' entries intact).', 'success');
        } else {
          toast('TAMPER DETECTED at ' + ((ver && ver.corrupted_entry_id) || 'chain'), 'error');
        }
      } catch (err) {
        toast(err && err.message ? err.message : 'Verification request failed', 'error');
      } finally {
        verifyBtn.disabled = false;
      }
    });
  }

  if (refreshBtn) {
    refreshBtn.addEventListener('click', async function () {
      await loadAllProjectMemory();
      toast('Memory OS synchronized.', 'info');
    });
  }

  if (selectEl) {
    selectEl.addEventListener('change', async function (ev) {
      state.projectId = ev.target.value;
      try {
        if (state.projectId) {
          localStorage.setItem('kobits.projectId', state.projectId);
        }
      } catch (ignoreErr) {}
      await loadAllProjectMemory();
    });
  }

  syncTabVisuals();

  try {
    var projects = [];
    try {
      var fetchedProjects = await get('/projects');
      if (Array.isArray(fetchedProjects)) {
        projects = fetchedProjects;
      } else if (fetchedProjects && Array.isArray(fetchedProjects.projects)) {
        projects = fetchedProjects.projects;
      }
    } catch (projErr) {
      projects = [];
    }

    var savedPid = '';
    try {
      savedPid = localStorage.getItem('kobits.projectId') || '';
    } catch (lsErr) {
      savedPid = '';
    }

    if (!projects.length) {
      var topSel = document.getElementById('ky-project-select');
      if (topSel && topSel.options && topSel.options.length > 0) {
        for (var oi = 0; oi < topSel.options.length; oi++) {
          var opt = topSel.options[oi];
          if (opt.value && opt.value !== '__new__') {
            projects.push({ id: opt.value, name: opt.textContent || opt.value });
          }
        }
      }
    }

    if (!projects.length) {
      projects = [{ id: savedPid || 'default', name: 'Primary Engineering Workspace' }];
    }

    state.projects = projects;
    var chosen = projects[0].id;
    if (savedPid) {
      for (var pi = 0; pi < projects.length; pi++) {
        if (projects[pi].id === savedPid) {
          chosen = savedPid;
          break;
        }
      }
    }
    state.projectId = chosen;

    if (selectEl) {
      selectEl.innerHTML = projects.map(function (p) {
        var selAttr = p.id === state.projectId ? ' selected' : '';
        return '<option value="' + esc(p.id) + '"' + selAttr + '>' + esc(p.name || p.id) + '</option>';
      }).join('');
    }

    await loadAllProjectMemory();
  } catch (e) {
    if (listEl) listEl.innerHTML = errorBox(e);
  }
}

export var renderMemoryInspectorView = render;
