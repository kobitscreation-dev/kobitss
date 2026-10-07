// Shared UI helpers: escaping, formatting, pills, icons, phases, diff rendering, modal, toast.

export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const DASH = '—';

// ── Icons (inline SVG, Lucide-style strokes) ──────────────────
const ICONS = {
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M10 21v-6h4v6"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
  arrowUp: '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>',
  arrowLeft: '<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>',
  sparkles: '<path d="m12 3 1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2z"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.64-6.36L21 8"/><path d="M21 3v5h-5"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  diff: '<path d="M12 3v14"/><path d="M5 10h14"/><path d="M5 21h14"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
  cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
};

export function icon(name, size = 16) {
  const body = ICONS[name] || ICONS.alert;
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

export function hydrateIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach((el) => {
    el.innerHTML = icon(el.dataset.icon, Number(el.dataset.size) || 17);
    el.style.display = 'inline-flex';
  });
}

// ── Formatting ────────────────────────────────────────────────
function parseDate(iso) {
  if (!iso) return null;
  // Backend may emit naive UTC timestamps; treat them as UTC.
  const s = /Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(s);
  return isNaN(d) ? null : d;
}

export function timeAgo(iso) {
  const d = parseDate(iso);
  if (!d) return DASH;
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 45) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)}d ago`;
  return d.toLocaleDateString();
}

export function duration(seconds) {
  if (seconds == null || isNaN(seconds)) return DASH;
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function secondsBetween(a, b) {
  const da = parseDate(a), db = parseDate(b);
  return da && db ? (db - da) / 1000 : null;
}

export const num = (v) => (v == null ? DASH : Number(v).toLocaleString());
export function compact(v) {
  if (v == null) return DASH;
  const n = Number(v);
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}
export const truncate = (s, n = 80) => (!s ? '' : s.length > n ? `${s.slice(0, n - 1)}…` : s);
export const titleCase = (s) => (s || '').toLowerCase().split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');

// ── Missions (Kyros Fluid Single-Loop Stages) ─────────────────
export const PHASES = [
  ['PLANNING', 'Plan'],
  ['IMPLEMENTATION', 'Code'],
  ['VALIDATION', 'Verify & Test'],
  ['DELIVERY_REVIEW', 'Deliver'],
];
const FLUID_PHASE_MAP = {
  INTAKE: 0,
  ANALYSIS: 0,
  PLANNING: 0,
  ARCHITECTURE_REVIEW: 0,
  IMPLEMENTATION: 1,
  VALIDATION: 2,
  SECURITY: 2,
  CODE_REVIEW: 2,
  DELIVERY_REVIEW: 3,
  DEPLOYMENT: 3,
  LEARNING: 3,
};
export const RUNNING = new Set(['ACTIVE', 'PLANNING', 'EXECUTING', 'READY']);
export const TERMINAL = new Set(['COMPLETED', 'FAILED', 'CANCELLED']);

const STATUS = {
  COMPLETED: ['success', 'Completed'],
  ACTIVE: ['accent', 'Running'],
  READY: ['accent', 'Starting'],
  PLANNING: ['accent', 'Planning'],
  EXECUTING: ['accent', 'Running'],
  AWAITING_APPROVAL: ['warning', 'Needs approval'],
  BLOCKED: ['warning', 'Blocked'],
  FAILED: ['error', 'Failed'],
  CANCELLED: ['', 'Cancelled'],
  PENDING: ['', 'Pending'],
  IN_PROGRESS: ['accent', 'In progress'],
  QUEUED: ['', 'Queued'],
  RUNNING: ['accent', 'Running'],
};

export function pill(status) {
  const [cls, label] = STATUS[status] || ['', titleCase(status || 'unknown')];
  return `<span class="pill ${cls}">${esc(label)}</span>`;
}

export function phaseIndex(phase) {
  if (phase && Object.prototype.hasOwnProperty.call(FLUID_PHASE_MAP, phase)) {
    return FLUID_PHASE_MAP[phase];
  }
  return PHASES.findIndex(([k]) => k === phase);
}

/** Same rule as the CLI's effective_progress: completed = 100, otherwise at least the phase position. */
export function progressOf(m) {
  if (m.status === 'COMPLETED') return 100;
  const idx = phaseIndex(m.phase);
  const byPhase = idx >= 0 ? Math.round((idx / PHASES.length) * 100) : 0;
  return Math.max(Number(m.progress) || 0, byPhase);
}

export function progressBar(m, width = '100%') {
  const pct = progressOf(m);
  const cls = m.status === 'COMPLETED' ? 'success' : m.status === 'FAILED' ? 'error' : '';
  return `<div class="bar ${cls}" style="width:${width}" title="${pct}%"><span style="width:${pct}%"></span></div>`;
}

function phaseState(i, current, status) {
  if (status === 'COMPLETED') return 'done';
  if (i < current) return 'done';
  if (i === current) return status === 'FAILED' || status === 'BLOCKED' ? 'failed' : 'current';
  return '';
}

export function phaseTracker(m) {
  const cur = phaseIndex(m.phase);
  return `<div class="phases">${PHASES.map(([, label], i) =>
    `<div class="phase ${phaseState(i, cur, m.status)}"><span class="dot"></span><span>${label}</span></div>`).join('')}</div>`;
}

export function phaseStepper(m) {
  const cur = phaseIndex(m.phase);
  return `<ol class="stepper">${PHASES.map(([, label], i) =>
    `<li class="step ${phaseState(i, cur, m.status)}"><span class="dot"></span>${label}</li>`).join('')}</ol>`;
}

export function initials(name) {
  return (name || '?').split(/[\s_]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
}

// ── Unified diff → table ──────────────────────────────────────
export function renderDiff(text) {
  if (!text) return '<div class="empty">No textual diff for this file.</div>';
  const rows = [];
  let oldLn = 0, newLn = 0;
  for (const line of text.split('\n')) {
    if (/^(diff --git|index |new file mode|deleted file mode|similarity|rename |--- |\+\+\+ |\\ No newline)/.test(line)) continue;
    const h = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)/.exec(line);
    if (h) {
      oldLn = Number(h[1]); newLn = Number(h[2]);
      rows.push(`<tr class="hunk"><td class="ln"></td><td>${esc(line)}</td></tr>`);
    } else if (line.startsWith('+')) {
      rows.push(`<tr class="add"><td class="ln">${newLn++}</td><td>${esc(line)}</td></tr>`);
    } else if (line.startsWith('-')) {
      rows.push(`<tr class="del"><td class="ln">${oldLn++}</td><td>${esc(line)}</td></tr>`);
    } else if (line.length || rows.length) {
      rows.push(`<tr><td class="ln">${newLn}</td><td>${esc(' ' + line.slice(1))}</td></tr>`);
      oldLn++; newLn++;
    }
  }
  return `<div class="diff"><table>${rows.join('')}</table></div>`;
}

// ── Feedback ──────────────────────────────────────────────────
export function skeleton(lines = 4) {
  return `<div class="card-body">${Array.from({ length: lines }, (_, i) =>
    `<div class="skeleton sk-line" style="width:${90 - i * 12}%"></div>`).join('')}</div>`;
}

export function emptyState(title, message, action = '', iconName = 'sparkles') {
  return `<div class="empty"><div class="icon">${icon(iconName, 40)}</div><h3>${esc(title)}</h3><p>${esc(message)}</p>${action}</div>`;
}

export function errorBox(err) {
  return `<div class="error-box">${icon('alert')} ${esc(err?.message || err)}</div>`;
}

export function toast(message, type = 'info', ms = 3500) {
  const host = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = `toast ${type === 'error' ? 'error' : ''}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

export function openModal(html) {
  closeModal();
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.id = 'modal';
  overlay.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeModal(); });
  document.body.appendChild(overlay);
  return overlay.querySelector('.modal');
}

export function closeModal() {
  document.getElementById('modal')?.remove();
}

document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
