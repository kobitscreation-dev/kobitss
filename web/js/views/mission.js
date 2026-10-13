import { get, post } from '../api.js';
import { poll, navigate } from '../core.js';
import {
  DASH, duration, esc, errorBox, num, phaseTracker, pill, progressBar,
  RUNNING, skeleton, timeAgo, toast, titleCase, renderDiff
} from '../ui.js';

let missionId;
let currentMission = null;

// Terminal WebSocket state
let terminalWs = null;
let terminalReconnectTimer = null;
let terminalReconnectAttempts = 0;
let terminalPingTimer = null;
let terminalIsCleaningUp = false;
let terminalAutoScroll = true;
let missionDebounceTimer = null;
let fallbackPollTimer = null;

function updateTerminalStatus(state, text) {
  const badge = document.getElementById('terminal-status-badge');
  const dot = document.getElementById('terminal-status-dot');
  const label = document.getElementById('terminal-status-text');
  if (!badge || !dot || !label) return;

  label.textContent = text;
  badge.className = 'pill';

  if (state === 'connected') {
    dot.style.background = 'var(--done-fg, #22c55e)';
    dot.style.boxShadow = '0 0 6px rgba(34, 197, 94, 0.6)';
    badge.style.borderColor = 'rgba(34, 197, 94, 0.3)';
    badge.style.color = 'var(--done-fg, #22c55e)';
  } else if (state === 'connecting' || state === 'reconnecting') {
    dot.style.background = 'var(--warn-fg, #eab308)';
    dot.style.boxShadow = '0 0 6px rgba(234, 179, 8, 0.6)';
    badge.style.borderColor = 'rgba(234, 179, 8, 0.3)';
    badge.style.color = 'var(--warn-fg, #eab308)';
  } else if (state === 'error') {
    dot.style.background = 'var(--err-fg, #ef4444)';
    dot.style.boxShadow = '0 0 6px rgba(239, 68, 68, 0.6)';
    badge.style.borderColor = 'rgba(239, 68, 68, 0.3)';
    badge.style.color = 'var(--err-fg, #ef4444)';
  } else {
    dot.style.background = 'var(--text-3, #71717a)';
    dot.style.boxShadow = 'none';
    badge.style.borderColor = 'var(--border, #27272a)';
    badge.style.color = 'var(--text-3, #71717a)';
  }
}

function formatTime(ts) {
  const d = ts ? new Date(typeof ts === 'number' && ts < 1e11 ? ts * 1000 : ts) : new Date();
  if (isNaN(d.getTime())) return new Date().toTimeString().slice(0, 8);
  return d.toTimeString().slice(0, 8);
}

function appendTerminalLine(payload) {
  const outEl = document.getElementById('terminal-output');
  if (!outEl) return;

  let type = 'system';
  let message = '';
  let timeStr = formatTime(payload?.timestamp);

  if (typeof payload === 'string') {
    message = payload;
  } else if (payload && typeof payload === 'object') {
    type = payload.type || 'system';
    if (payload.message) {
      message = payload.message;
    } else if (payload.content) {
      message = payload.content;
    } else if (payload.command) {
      message = payload.command;
    } else {
      message = JSON.stringify(payload);
    }
  }

  const lineEl = document.createElement('div');
  lineEl.className = 'terminal-line';
  lineEl.style.marginBottom = '4px';
  lineEl.style.fontFamily = 'inherit';

  let tagHtml = '';
  let contentHtml = '';

  switch (type) {
    case 'system':
      tagHtml = `<span style="color:#38bdf8;font-weight:600;">[SYS]</span>`;
      contentHtml = `<span style="color:#e0f2fe;">${esc(message)}</span>`;
      break;

    case 'terminal_output': {
      const cmd = payload.command ? `$ ${payload.command}\n` : '';
      const stdout = payload.stdout || '';
      const stderr = payload.stderr || payload.error || '';
      const code = payload.exit_code !== undefined ? payload.exit_code : 0;
      tagHtml = `<span style="color:#a78bfa;font-weight:600;">[EXEC]</span>`;
      let inner = '';
      if (cmd) inner += `<div style="color:#ffffff;font-weight:600;">${esc(cmd)}</div>`;
      if (stdout) inner += `<div style="color:#d4d4d8;">${esc(stdout)}</div>`;
      if (stderr) inner += `<div style="color:#f87171;">${esc(stderr)}</div>`;
      if (code !== 0) inner += `<div style="color:#f87171;font-size:11px;">(process exited with code ${code})</div>`;
      contentHtml = inner;
      break;
    }

    case 'task_start':
      tagHtml = `<span style="color:#fbbf24;font-weight:600;">[TASK]</span>`;
      contentHtml = `<span style="color:#fef08a;">${esc(payload.message || `Started task: ${payload.task_title || ''} (${payload.agent || 'Specialist'})`)}</span>`;
      break;

    case 'task_complete':
      tagHtml = `<span style="color:#4ade80;font-weight:600;">[DONE]</span>`;
      contentHtml = `<span style="color:#bbf7d0;">${esc(payload.message || `Completed task: ${payload.task_title || ''}`)}</span>`;
      break;

    case 'task_fail':
      tagHtml = `<span style="color:#f87171;font-weight:600;">[FAIL]</span>`;
      contentHtml = `<span style="color:#fecaca;">${esc(payload.message || `Failed task: ${payload.task_title || ''} - ${payload.error || ''}`)}</span>`;
      break;

    case 'task_blocked':
      tagHtml = `<span style="color:#fb923c;font-weight:600;">[BLOCKED]</span>`;
      contentHtml = `<span style="color:#fed7aa;">${esc(payload.message || `Task blocked: ${payload.task_title || ''}`)}</span>`;
      break;

    case 'mission_state':
      tagHtml = `<span style="color:#c084fc;font-weight:600;">[STATE]</span>`;
      contentHtml = `<span style="color:#f3e8ff;">${esc(payload.message || `Mission status updated: ${payload.status || ''} [Phase: ${payload.phase || ''}]`)}</span>`;
      break;

    case 'phase_start':
      tagHtml = `<span style="color:#60a5fa;font-weight:600;">[PHASE]</span>`;
      contentHtml = `<span style="color:#dbeafe;">${esc(payload.message || `Entering phase: ${payload.phase || ''}`)}</span>`;
      break;

    case 'phase_complete':
      tagHtml = `<span style="color:#34d399;font-weight:600;">[PHASE]</span>`;
      contentHtml = `<span style="color:#a7f3d0;">${esc(payload.message || `Phase completed: ${payload.phase || ''}`)}</span>`;
      break;

    case 'live_steering_injected':
      tagHtml = `<span style="color:#f97316;font-weight:600;">[STEER]</span>`;
      contentHtml = `<span style="color:#ffedd5;">${esc(payload.message || `Live directive injected: ${JSON.stringify(payload.directives || payload)}`)}</span>`;
      break;

    case 'debate_round':
    case 'debate_start':
      tagHtml = `<span style="color:#2dd4bf;font-weight:600;">[DEBATE]</span>`;
      contentHtml = `<span style="color:#ccfbf1;">${esc(message)}</span>`;
      break;

    case 'user_input':
      tagHtml = `<span style="color:#22c55e;font-weight:600;">[USER]</span>`;
      contentHtml = `<span style="color:#86efac;font-weight:600;">${esc(message)}</span>`;
      break;

    case 'error':
      tagHtml = `<span style="color:#ef4444;font-weight:600;">[ERR]</span>`;
      contentHtml = `<span style="color:#fca5a5;">${esc(message)}</span>`;
      break;

    default:
      tagHtml = `<span style="color:#a1a1aa;font-weight:600;">[LOG]</span>`;
      contentHtml = `<span style="color:#d4d4d8;">${esc(message)}</span>`;
      break;
  }

  lineEl.innerHTML = `<span style="color:var(--text-4, #52525b);margin-right:8px;user-select:none;">${timeStr}</span> ${tagHtml} ${contentHtml}`;
  outEl.appendChild(lineEl);

  // Enforce rolling DOM buffer limit to eliminate memory leaks and DOM bloat
  const MAX_TERMINAL_LINES = 1000;
  while (outEl.children.length > MAX_TERMINAL_LINES) {
    outEl.removeChild(outEl.firstChild);
  }

  if (terminalAutoScroll) {
    outEl.scrollTop = outEl.scrollHeight;
  }
}

function debounceReloadMission() {
  if (missionDebounceTimer) clearTimeout(missionDebounceTimer);
  missionDebounceTimer = setTimeout(async () => {
    const updated = await loadMission();
    if (updated) {
      loadDiff();
      loadActivity();
    }
  }, 250);
}

export function cleanupTerminal() {
  terminalIsCleaningUp = true;
  if (terminalReconnectTimer) {
    clearTimeout(terminalReconnectTimer);
    terminalReconnectTimer = null;
  }
  if (terminalPingTimer) {
    clearInterval(terminalPingTimer);
    terminalPingTimer = null;
  }
  if (fallbackPollTimer) {
    clearInterval(fallbackPollTimer);
    fallbackPollTimer = null;
  }
  if (terminalWs) {
    try {
      terminalWs.close();
    } catch (_) {}
    terminalWs = null;
  }
  updateTerminalStatus('disconnected', 'Offline');
}

function scheduleTerminalReconnect(id) {
  if (terminalIsCleaningUp) return;
  if (terminalReconnectTimer) clearTimeout(terminalReconnectTimer);

  terminalReconnectAttempts++;
  const delay = Math.min(1000 * Math.pow(1.5, terminalReconnectAttempts), 10000);
  updateTerminalStatus('reconnecting', `Reconnecting in ${(delay / 1000).toFixed(1)}s...`);

  terminalReconnectTimer = setTimeout(() => {
    if (!terminalIsCleaningUp) {
      connectTerminalWs(id);
    }
  }, delay);
}

function getTerminalWsUrl(id) {
  let proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  let host = location.host;

  if (typeof window !== 'undefined') {
    if (window.KOBITS_WS_URL) {
      const base = window.KOBITS_WS_URL.replace(/^http/, 'ws').replace(/\/+$/, '');
      return `${base}/ws/terminal/${encodeURIComponent(id)}`;
    }
    if (window.KOBITS_API_URL) {
      try {
        const u = new URL(window.KOBITS_API_URL, location.href);
        proto = u.protocol === 'https:' ? 'wss:' : 'ws:';
        host = u.host;
      } catch (_) {}
    }
  }

  const token = typeof localStorage !== 'undefined' ? localStorage.getItem('kobits_token') : null;
  const tokenQuery = token ? `?token=${encodeURIComponent(token)}` : '';
  return `${proto}//${host}/ws/terminal/${encodeURIComponent(id)}${tokenQuery}`;
}

function connectTerminalWs(id) {
  if (terminalWs) {
    cleanupTerminal();
  }
  terminalIsCleaningUp = false;

  if (typeof WebSocket === 'undefined' || !location.host) {
    appendTerminalLine({ type: 'system', message: 'WebSocket is not supported in this runtime environment.' });
    return;
  }

  const wsUrl = getTerminalWsUrl(id);

  updateTerminalStatus('connecting', 'Connecting...');
  appendTerminalLine({ type: 'system', message: `Connecting to live terminal at /ws/terminal/${id}...` });

  try {
    terminalWs = new WebSocket(wsUrl);
  } catch (err) {
    updateTerminalStatus('error', 'Connection Error');
    appendTerminalLine({ type: 'error', message: `Failed to open terminal WebSocket: ${err.message}` });
    scheduleTerminalReconnect(id);
    return;
  }

  terminalWs.addEventListener('open', () => {
    terminalReconnectAttempts = 0;
    updateTerminalStatus('connected', 'Connected');

    // Clear fallback HTTP poll when live WebSocket is active
    if (fallbackPollTimer) {
      clearInterval(fallbackPollTimer);
      fallbackPollTimer = null;
    }

    if (terminalPingTimer) clearInterval(terminalPingTimer);
    terminalPingTimer = setInterval(() => {
      if (terminalWs && terminalWs.readyState === WebSocket.OPEN) {
        try {
          terminalWs.send(JSON.stringify({ type: 'ping' }));
        } catch (_) {}
      }
    }, 25000);
  });

  terminalWs.addEventListener('message', (ev) => {
    try {
      let data;
      try {
        data = JSON.parse(ev.data);
      } catch (_) {
        data = { type: 'log', message: ev.data };
      }

      if (data && data.type === 'pong') return;

      if (data && data.type === 'clear') {
        const outEl = document.getElementById('terminal-output');
        if (outEl) outEl.innerHTML = '';
        appendTerminalLine({ type: 'system', message: 'Terminal output buffer cleared.' });
        return;
      }

      appendTerminalLine(data);

      // Instantly synchronize mission telemetry on state change events
      if (data && ['mission_state', 'task_start', 'task_complete', 'task_fail', 'task_blocked', 'phase_start', 'phase_complete', 'mission_complete'].includes(data.type)) {
        debounceReloadMission();
      }
    } catch (e) {
      appendTerminalLine({ type: 'log', message: ev.data });
    }
  });

  terminalWs.addEventListener('close', (ev) => {
    if (terminalPingTimer) clearInterval(terminalPingTimer);
    if (!terminalIsCleaningUp) {
      updateTerminalStatus('disconnected', 'Disconnected');
      appendTerminalLine({ type: 'system', message: `Terminal connection closed (code ${ev.code}). Reconnecting...` });
      scheduleTerminalReconnect(id);

      // Start quiet low-frequency fallback poll only while socket is reconnecting
      if (!fallbackPollTimer && currentMission && RUNNING.has(currentMission.status)) {
        fallbackPollTimer = setInterval(debounceReloadMission, 15000);
      }
    }
  });

  terminalWs.addEventListener('error', () => {
    updateTerminalStatus('error', 'Error');
  });
}

function sendTerminalInput(text) {
  if (!text || !text.trim()) return;
  const clean = text.trim();
  const input = document.getElementById('terminal-input');

  if (clean.toLowerCase() === 'clear') {
    const outEl = document.getElementById('terminal-output');
    if (outEl) outEl.innerHTML = '';
    appendTerminalLine({ type: 'system', message: 'Terminal output buffer cleared.' });
    return;
  }

  appendTerminalLine({ type: 'user_input', message: clean });

  if (terminalWs && terminalWs.readyState === WebSocket.OPEN) {
    try {
      terminalWs.send(JSON.stringify({ type: 'input', message: clean, command: clean }));
    } catch (e) {
      appendTerminalLine({ type: 'error', message: `Failed to dispatch input: ${e.message}` });
      if (input && !input.value) input.value = clean;
    }
  } else {
    appendTerminalLine({ type: 'error', message: 'Terminal WebSocket is not connected. Attempting reconnection...' });
    if (input && !input.value) input.value = clean;
    connectTerminalWs(missionId);
  }
}

function renderTerminal() {
  return `
    <div class="card" id="mission-terminal-card" style="margin-bottom: 16px;">
      <div class="card-head" style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
        <div style="display: flex; align-items: center; gap: 8px;">
          <h3 style="margin: 0;">Live Terminal</h3>
          <span id="terminal-status-badge" class="pill" style="font-size: 11px; padding: 2px 8px; display: inline-flex; align-items: center; gap: 6px;">
            <span id="terminal-status-dot" style="display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--text-3, #71717a);"></span>
            <span id="terminal-status-text">Disconnected</span>
          </span>
        </div>
        <div class="row" style="gap: 6px;">
          <button class="btn btn-ghost btn-sm" id="btn-terminal-autoscroll" title="Toggle Auto-Scroll" style="font-size: 11px; padding: 3px 8px;">Auto-scroll: ON</button>
          <button class="btn btn-ghost btn-sm" id="btn-terminal-clear" title="Clear Terminal Output" style="font-size: 11px; padding: 3px 8px;">Clear</button>
          <button class="btn btn-ghost btn-sm" id="btn-terminal-reconnect" title="Reconnect WebSocket" style="font-size: 11px; padding: 3px 8px;">Reconnect</button>
        </div>
      </div>
      <div class="card-body" style="padding: 0;">
        <div id="terminal-output" class="mono" style="
          background: #09090b;
          color: #e4e4e7;
          font-family: var(--font-mono, 'JetBrains Mono', Consolas, monospace);
          font-size: 12px;
          line-height: 1.5;
          padding: 12px 16px;
          min-height: 240px;
          max-height: 380px;
          overflow-y: auto;
          white-space: pre-wrap;
          word-break: break-all;
          border-bottom: 1px solid var(--border, #222226);
        "></div>
        <form id="terminal-form" style="display: flex; align-items: center; gap: 8px; padding: 10px 14px; background: rgba(0,0,0,0.25);" autocomplete="off">
          <span class="mono faint" style="user-select: none; font-weight: 600;">$</span>
          <input 
            type="text" 
            id="terminal-input" 
            placeholder="Type directive, instruction, or command (e.g. 'status', 'clear', 'run tests')..." 
            style="
              flex: 1;
              background: transparent;
              border: none;
              outline: none;
              color: var(--text-1, #ffffff);
              font-family: var(--font-mono, 'JetBrains Mono', monospace);
              font-size: 12px;
            "
          />
          <button type="submit" class="btn btn-primary btn-sm" id="btn-terminal-send" style="padding: 4px 12px; font-size: 11.5px;">Execute</button>
        </form>
      </div>
    </div>
  `;
}

function wireTerminal() {
  const form = document.getElementById('terminal-form');
  const input = document.getElementById('terminal-input');
  const btnClear = document.getElementById('btn-terminal-clear');
  const btnAuto = document.getElementById('btn-terminal-autoscroll');
  const btnReconnect = document.getElementById('btn-terminal-reconnect');
  const outEl = document.getElementById('terminal-output');

  form?.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!input) return;
    const val = input.value;
    input.value = '';
    sendTerminalInput(val);
  });

  btnClear?.addEventListener('click', () => {
    if (outEl) outEl.innerHTML = '';
    appendTerminalLine({ type: 'system', message: 'Terminal output buffer cleared.' });
  });

  btnAuto?.addEventListener('click', () => {
    terminalAutoScroll = !terminalAutoScroll;
    if (btnAuto) {
      btnAuto.textContent = terminalAutoScroll ? 'Auto-scroll: ON' : 'Auto-scroll: OFF';
    }
    if (terminalAutoScroll && outEl) {
      outEl.scrollTop = outEl.scrollHeight;
    }
  });

  btnReconnect?.addEventListener('click', () => {
    if (missionId) {
      connectTerminalWs(missionId);
    }
  });

  if (outEl) {
    outEl.addEventListener('scroll', () => {
      const isAtBottom = outEl.scrollHeight - outEl.scrollTop - outEl.clientHeight <= 25;
      terminalAutoScroll = isAtBottom;
      if (btnAuto) {
        btnAuto.textContent = terminalAutoScroll ? 'Auto-scroll: ON' : 'Auto-scroll: OFF';
      }
    });
  }
}

function renderHeader(m) {
  let actions = '';
  if (m.status === 'AWAITING_APPROVAL') {
    actions = `<button class="btn btn-primary" id="btn-approve">Approve</button>
               <button class="btn btn-danger" id="btn-cancel">Reject</button>`;
  } else if (m.status === 'FAILED') {
    actions = `<button class="btn btn-primary" id="btn-retry">Retry</button>`;
  } else if (RUNNING.has(m.status)) {
    actions = `<button class="btn btn-ghost" id="btn-cancel">Cancel</button>`;
  }

  const badges = [];
  if (m.risk_level) badges.push(`<span class="pill warning">Risk: ${esc(m.risk_level)}</span>`);
  if (m.active_branch) badges.push(`<span class="pill">Branch: ${esc(m.active_branch)}</span>`);

  return `
    <a href="#/missions" class="faint" style="text-decoration:none; display:inline-block; margin-bottom:16px;">&larr; Missions</a>
    <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom: 16px;">
      <div>
        <h1 style="margin: 0 0 8px 0;">${esc(m.title)}</h1>
        <div class="row" style="gap: 8px;">
          ${pill(m.status)}
          <span class="faint">${esc(titleCase(m.phase))}</span>
          ${badges.join('')}
        </div>
      </div>
      <div class="row" style="gap: 8px;">
        ${actions}
      </div>
    </div>
    <div style="margin-bottom: 24px;">
      ${progressBar(m)}
    </div>
    <div style="margin-bottom: 24px;">
      ${phaseTracker(m)}
    </div>
  `;
}

function renderDetails(m) {
  const dur = m.completed_at && m.created_at ? duration((new Date(m.completed_at) - new Date(m.created_at))/1000) : DASH;
  return `
    <div class="card" style="margin-bottom: 16px;">
      <div class="card-head"><h3>Details</h3></div>
      <div class="card-body">
        <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 8px;">
          <div class="faint">Created</div><div>${timeAgo(m.created_at)}</div>
          <div class="faint">Duration</div><div>${dur}</div>
          <div class="faint">Retry Count</div><div>${m.retry_count || 0}</div>
          <div class="faint">Priority</div><div>${esc(m.priority || DASH)}</div>
          <div class="faint">Risk Level</div><div>${esc(m.risk_level || DASH)}</div>
        </div>
      </div>
    </div>
  `;
}

function renderTasks(tasks) {
  if (!tasks || !tasks.length) return '<div class="card-body faint">No tasks</div>';
  return tasks.map(t => `
    <div class="list-row">
      <span>${pill(t.status)}</span>
      <span style="flex:1">
        <div class="title">${esc(t.title)}</div>
        <div class="sub">${esc(t.agent_role || 'Agent')} ${t.attempt > 1 ? `(Attempt ${t.attempt})` : ''}</div>
      </span>
    </div>
  `).join('');
}

function wireActions() {
  document.getElementById('btn-approve')?.addEventListener('click', async () => {
    try {
      await post(`/missions/${missionId}/approve`);
      toast('Mission approved', 'success');
      loadMission();
    } catch (e) {
      toast(e.message, 'error');
    }
  });

  document.getElementById('btn-cancel')?.addEventListener('click', async () => {
    try {
      await post(`/missions/${missionId}/cancel`);
      toast('Mission cancelled');
      loadMission();
    } catch (e) {
      toast(e.message, 'error');
    }
  });

  document.getElementById('btn-retry')?.addEventListener('click', async () => {
    try {
      await post(`/missions/${missionId}/retry`);
      toast('Mission retrying', 'success');
      loadMission();
    } catch (e) {
      toast(e.message, 'error');
    }
  });

  document.getElementById('btn-apply')?.addEventListener('click', async () => {
    try {
      await post(`/missions/${missionId}/apply`, { dry_run: false, force: false });
      toast('Changes applied successfully', 'success');
    } catch (e) {
      toast(e.message, 'error');
    }
  });
}

async function loadMission() {
  try {
    const m = await get(`/missions/${missionId}`);
    currentMission = m;
    
    document.getElementById('mission-header').innerHTML = renderHeader(m);
    document.getElementById('mission-objective').textContent = m.objective || 'No objective provided.';
    document.getElementById('mission-tasks').innerHTML = renderTasks(m.tasks);
    document.getElementById('mission-details').innerHTML = renderDetails(m);
    
    wireActions();
    return m;
  } catch (e) {
    document.getElementById('mission-header').innerHTML = errorBox(e);
    return null;
  }
}

async function loadDiff() {
  const container = document.getElementById('mission-changes');
  try {
    const d = await get(`/missions/${missionId}/diff`);
    if (d && d.diff_text) {
      let applyBtn = currentMission?.status === 'COMPLETED' ? `<button class="btn btn-primary btn-sm" id="btn-apply" style="margin-bottom:12px;">Apply Changes</button>` : '';
      container.innerHTML = `
        <div class="card-head"><h3>Changes</h3></div>
        <div class="card-body">
          <p class="faint" style="margin: 0 0 12px 0;">Files changed: ${d.files ? d.files.length : 0}</p>
          ${applyBtn}
          ${renderDiff(d.diff_text)}
        </div>
      `;
      wireActions(); // to wire the apply button
    } else {
      container.innerHTML = `<div class="card-head"><h3>Changes</h3></div><div class="card-body faint">No sandbox changes yet</div>`;
    }
  } catch (e) {
    container.innerHTML = `<div class="card-head"><h3>Changes</h3></div><div class="card-body faint">No sandbox changes yet</div>`;
  }
}

async function loadActivity() {
  const container = document.getElementById('mission-activity');
  try {
    const act = await get(`/missions/${missionId}/activity`);
    if (act && act.length > 0) {
      container.innerHTML = `
        <div class="card-head"><h3>Activity</h3></div>
        <div class="list">
          ${act.map(a => `
            <div class="list-row">
              <span class="faint mono">${timeAgo(a.timestamp)}</span>
              <span style="flex:1">
                <div>${esc(a.description)}</div>
                <div class="sub">${esc(a.agent_name || 'System')}</div>
              </span>
            </div>
          `).join('')}
        </div>
      `;
    } else {
      container.innerHTML = `<div class="card-head"><h3>Activity</h3></div><div class="card-body faint">No activity yet</div>`;
    }
  } catch (e) {
    container.innerHTML = `<div class="card-head"><h3>Activity</h3></div><div class="card-body">${errorBox(e)}</div>`;
  }
}

function handleRouteCleanup() {
  const currentHash = (location.hash || '').replace(/^#\/?/, '');
  if (!currentHash.includes(missionId)) {
    cleanupTerminal();
    window.removeEventListener('hashchange', handleRouteCleanup);
  }
}

export async function render(id) {
  cleanupTerminal();
  window.removeEventListener('hashchange', handleRouteCleanup);
  missionId = id;
  const view = document.getElementById('view');
  
  view.innerHTML = `
    <div class="page fade-in">
      <div id="mission-header">${skeleton(3)}</div>
      
      <div class="grid-2">
        <div class="stack">
          <div class="card">
            <div class="card-head"><h3>Objective</h3></div>
            <div class="card-body" id="mission-objective" style="white-space: pre-wrap;">${skeleton(2)}</div>
          </div>
          
          ${renderTerminal()}
          
          <div class="card">
            <div class="card-head"><h3>Tasks</h3></div>
            <div class="list" id="mission-tasks">${skeleton(4)}</div>
          </div>
        </div>
        
        <div class="stack">
          <div id="mission-details">
            <div class="card"><div class="card-head"><h3>Details</h3></div><div class="card-body">${skeleton(3)}</div></div>
          </div>
          
          <div class="card" id="mission-changes">
            <div class="card-head"><h3>Changes</h3></div>
            <div class="card-body">${skeleton(4)}</div>
          </div>
          
          <div class="card" id="mission-activity">
            <div class="card-head"><h3>Activity</h3></div>
            <div class="card-body">${skeleton(3)}</div>
          </div>
        </div>
      </div>
    </div>
  `;
  
  wireTerminal();
  connectTerminalWs(missionId);

  window.addEventListener('hashchange', handleRouteCleanup);

  const m = await loadMission();
  if (m) {
    loadDiff();
    loadActivity();
  }
}
