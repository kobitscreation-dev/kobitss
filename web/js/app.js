// ============================================================================
// KOBITS — KYROS-STYLE MONOCHROME ENGINEERING DASHBOARD
// 100% Real Database Telemetry (/api/v1/org/dashboard, /missions/{id}/diff, /apply)
// ============================================================================

import { get, post } from './api.js';
import {
  DASH, duration, esc, num, compact, PHASES, phaseIndex,
  renderDiff, RUNNING, timeAgo, titleCase, truncate,
} from './ui.js';

const state = {
  tab: 'overview',          // overview | milestones | deliverables | team | reports
  projectId: localStorage.getItem('kobits.projectId') || '',
  delivFilter: 'all',       // all | delivered | progress | upcoming
  teamFilter: 'all',        // all | active
  teamQuery: '',
  selectedMissionId: null,  // inline switcher on Overview Current Sprint card
  dashboard: null,
};

function notify(msg, type = 'info') {
  const host = document.getElementById('toasts');
  if (!host) return;
  const el = document.createElement('div');
  el.className = `ky-toast ${type === 'error' ? 'error' : ''}`;
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => el.remove(), 3600);
}

// ── Formatting & Kyros Status Helpers ───────────────────────────────────────

function fmtUsd(v) {
  if (v === null || v === undefined || isNaN(v)) return DASH;
  const n = Number(v);
  if (n >= 1000) return `$${(n / 1000).toFixed(1)}K`;
  return `$${n.toFixed(2)}`;
}

function kyrosDeliverableBadge(status) {
  const s = (status || '').toUpperCase();
  if (s === 'COMPLETED' || s === 'DELIVERED') {
    return `<span class="ky-badge ky-badge-done">Delivered</span>`;
  }
  if (s === 'AWAITING_APPROVAL' || s === 'REVIEW') {
    return `<span class="ky-badge ky-badge-warn">In Review</span>`;
  }
  if (s === 'ACTIVE' || s === 'EXECUTING' || s === 'PLANNING' || s === 'IN_PROGRESS' || s === 'READY') {
    return `<span class="ky-badge ky-badge-active">Building</span>`;
  }
  if (s === 'FAILED' || s === 'BLOCKED') {
    return `<span class="ky-badge ky-badge-err">${s === 'BLOCKED' ? 'Blocked' : 'Failed'}</span>`;
  }
  return `<span class="ky-badge">Upcoming</span>`;
}

function kyrosMilestoneBadge(status) {
  const s = (status || '').toUpperCase();
  if (s === 'COMPLETED') {
    return `<span class="ky-badge ky-badge-done">Complete</span>`;
  }
  if (s === 'ACTIVE' || s === 'EXECUTING' || s === 'PLANNING' || s === 'IN_PROGRESS' || s === 'READY') {
    return `<span class="ky-badge ky-badge-active">Active</span>`;
  }
  if (s === 'AWAITING_APPROVAL' || s === 'REVIEW') {
    return `<span class="ky-badge ky-badge-warn">In Review</span>`;
  }
  if (s === 'FAILED' || s === 'BLOCKED') {
    return `<span class="ky-badge ky-badge-err">${s === 'BLOCKED' ? 'Blocked' : 'Failed'}</span>`;
  }
  return `<span class="ky-badge">Upcoming</span>`;
}

function taskStatusBadge(status) {
  const s = (status || '').toUpperCase();
  if (s === 'COMPLETED') return `<span class="ky-badge ky-badge-done">Done</span>`;
  if (s === 'AWAITING_APPROVAL' || s === 'REVIEW') return `<span class="ky-badge ky-badge-warn">Review</span>`;
  if (s === 'IN_PROGRESS' || s === 'RUNNING' || s === 'ACTIVE') return `<span class="ky-badge ky-badge-active">Building</span>`;
  if (s === 'FAILED') return `<span class="ky-badge ky-badge-err">Failed</span>`;
  if (s === 'BLOCKED') return `<span class="ky-badge ky-badge-warn">Blocked</span>`;
  return `<span class="ky-badge">Queued</span>`;
}

function taskNodeIcon(status) {
  const s = (status || '').toUpperCase();
  if (s === 'COMPLETED') {
    return `<span class="ky-task-icon done" title="Done">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
    </span>`;
  }
  if (s === 'AWAITING_APPROVAL' || s === 'REVIEW') {
    return `<span class="ky-task-icon" title="Review">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>
    </span>`;
  }
  if (s === 'IN_PROGRESS' || s === 'RUNNING' || s === 'ACTIVE') {
    return `<span class="ky-task-icon active" title="Building">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/></svg>
    </span>`;
  }
  if (s === 'FAILED') {
    return `<span class="ky-task-icon failed" title="Failed">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </span>`;
  }
  return `<span class="ky-task-icon" title="Queued">
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/></svg>
  </span>`;
}

function timelineNodeIcon(isDone, isLive, isFail) {
  if (isDone) {
    return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
  }
  if (isLive) {
    return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>`;
  }
  if (isFail) {
    return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`;
  }
  return `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/></svg>`;
}

function deliverableIcon(status) {
  const s = (status || '').toUpperCase();
  if (s === 'COMPLETED' || s === 'DELIVERED') {
    return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;color:var(--text-1)"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>`;
  }
  if (s === 'AWAITING_APPROVAL' || s === 'REVIEW') {
    return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;color:var(--text-2)"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`;
  }
  if (s === 'ACTIVE' || s === 'EXECUTING' || s === 'PLANNING' || s === 'IN_PROGRESS') {
    return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;color:var(--text-1)"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/></svg>`;
  }
  return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;color:var(--text-3)"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`;
}

function renderPhaseStrip(phase, status) {
  const cur = phaseIndex(phase);
  return `<div class="ky-phase-strip">${PHASES.map(([, label], idx) => {
    let cls = '';
    if (status === 'COMPLETED' || idx < cur) cls = 'done';
    else if (idx === cur) cls = (status === 'FAILED' || status === 'BLOCKED') ? 'failed' : 'current';
    return `<span class="ky-phase-pill ${cls}">${esc(label)}</span>`;
  }).join('')}</div>`;
}

// ── Data Fetching ───────────────────────────────────────────────────────────

async function fetchDashboard(silent = false) {
  const view = document.getElementById('view') || document.getElementById('main-content');
  if (!view) return;
  if (!silent && !state.dashboard) {
    view.innerHTML = `<div class="ky-card"><div class="ky-eyebrow">LOADING WORKSPACE</div><p style="color:var(--text-2);margin:8px 0 0">Synchronizing live engineering telemetry…</p></div>`;
  }
  try {
    const q = state.projectId ? `?project_id=${encodeURIComponent(state.projectId)}` : '';
    const data = await get(`/org/dashboard${q}`);
    state.dashboard = data;
    if (data?.project?.id) {
      state.projectId = data.project.id;
      localStorage.setItem('kobits.projectId', state.projectId);
    }
    populateProjectSelect(data);
    renderDashboard();
  } catch (err) {
    if (!silent) {
      view.innerHTML = `<div class="ky-card" style="border-color:var(--err-border)">
        <div class="ky-eyebrow" style="color:var(--err-fg)">TELEMETRY ERROR</div>
        <p style="margin:8px 0 0">${esc(err.message || String(err))}</p>
      </div>`;
    }
  }
}

function populateProjectSelect(data) {
  const sel = document.getElementById('ky-project-select');
  if (!sel || !data?.projects) return;
  const activeId = data.project?.id || '';
  sel.innerHTML = data.projects.map((p) =>
    `<option value="${esc(p.id)}" ${p.id === activeId ? 'selected' : ''}>${esc(p.name)}</option>`
  ).join('');
}

// ── Main Kyros View Renderer ────────────────────────────────────────────────

function renderDashboard() {
  const view = document.getElementById('view') || document.getElementById('main-content');
  if (!view) return;
  const d = state.dashboard;
  if (!d || !d.project) {
    view.innerHTML = `<div class="ky-card" style="text-align:center;padding:48px 24px">
      <div class="ky-eyebrow">WORKSPACE EMPTY</div>
      <h2 style="margin:8px 0">No Engineering Projects Yet</h2>
      <p style="color:var(--text-2);margin:0">Run a mission via the Kobits CLI (<code style="font-family:var(--font-mono)">kobits run "..."</code>) to populate your dashboard.</p>
    </div>`;
    return;
  }

  const proj = d.project;
  const pct = d.progress?.percent ?? 0;
  // Tag each mission with its chronological Sprint number (Sprint 1 = oldest, Sprint N = newest)
  const rawMilestones = d.milestones || [];
  const totalMissions = rawMilestones.length;
  const milestones = rawMilestones.map((m, idx) => ({
    ...m,
    sprintNumber: totalMissions - idx,
  }));

  view.innerHTML = `
    <!-- Kyros Project Header & Master Progress Bar -->
    <section class="ky-proj-header">
      <div class="ky-proj-top">
        <div>
          <h1 class="ky-proj-title">${esc(proj.name)}</h1>
          <div class="ky-proj-sub">
            <span>${esc(proj.description || 'Autonomous Software Engineering Platform')}</span>
          </div>
        </div>
        <div class="ky-proj-pct">${d.progress?.percent != null ? `${pct}%` : DASH}</div>
      </div>
      <div class="ky-master-bar" title="Completion: ${pct}%">
        <span style="width:${pct}%"></span>
      </div>
    </section>

    <!-- Exact 5 Kyros Dashboard Tabs -->
    <nav class="ky-tabs" role="tablist" aria-label="Dashboard sections">
      <button type="button" class="ky-tab ${state.tab === 'overview' ? 'active' : ''}" data-tab="overview">
        <span>Overview</span>
      </button>
      <button type="button" class="ky-tab ${state.tab === 'milestones' ? 'active' : ''}" data-tab="milestones">
        <span>Milestones</span>
      </button>
      <button type="button" class="ky-tab ${state.tab === 'deliverables' ? 'active' : ''}" data-tab="deliverables">
        <span>Deliverables</span>
      </button>
      <button type="button" class="ky-tab ${state.tab === 'team' ? 'active' : ''}" data-tab="team">
        <span>Team</span>
      </button>
      <button type="button" class="ky-tab ${state.tab === 'reports' ? 'active' : ''}" data-tab="reports">
        <span>Reports</span>
      </button>
    </nav>

    <!-- Tab Body -->
    <div id="ky-tab-body">
      ${renderActiveTab(d, milestones)}
    </div>
  `;

  wireTabEvents();
}

function renderActiveTab(d, milestones) {
  switch (state.tab) {
    case 'milestones':
      return renderMilestonesTab(milestones);
    case 'deliverables':
      return renderDeliverablesTab(milestones);
    case 'team':
      return renderTeamTab(d);
    case 'reports':
      return renderReportsTab(d, milestones);
    case 'overview':
    default:
      return renderOverviewTab(d, milestones);
  }
}

// ── TAB 1: OVERVIEW (Exact Kyros Layout) ────────────────────────────────────

function renderOverviewTab(d, milestones) {
  const pct = d.progress?.percent;
  const totalTasks = Number(d.progress?.total_tasks || 0);
  const completedTasks = Number(d.progress?.completed_tasks || 0);
  const remainingTasks = Math.max(0, totalTasks - completedTasks);
  const spend = d.spend || {};

  // Deliverables count: missions that are COMPLETED or have generated sandbox files
  const deliveredCount = milestones.filter(
    (m) => m.status === 'COMPLETED' || (m.files_changed && m.files_changed > 0)
  ).length;
  const totalMilestones = milestones.length || (d.missions?.total ?? 0);

  // Pick mission for Current Sprint card: user selection -> active -> latest with tasks
  const selectedMission = milestones.find((m) => m.mission_id === state.selectedMissionId)
    || (d.current ? milestones.find((m) => m.mission_id === d.current.mission_id) || d.current : null)
    || milestones[0]
    || null;

  const sprintTasks = selectedMission?.tasks || [];
  const sprintDone = selectedMission?.tasks_done ?? sprintTasks.filter((t) => t.status === 'COMPLETED').length;
  const sprintTotal = selectedMission?.tasks_total ?? sprintTasks.length;
  const sprintNum = selectedMission?.sprintNumber || milestones.length || 1;

  // Recent deliverables on Overview
  const recentDeliverables = milestones.slice(0, 6);

  return `
    <!-- 4 Kyros KPI Stat Cards (COMPLETE | DELIVERABLES | SPENT | REMAINING) -->
    <div class="ky-kpi-grid">
      <div class="ky-kpi-card">
        <div class="ky-kpi-label">COMPLETE</div>
        <div class="ky-kpi-val">${pct != null ? `${pct}%` : DASH}</div>
      </div>
      <div class="ky-kpi-card">
        <div class="ky-kpi-label">DELIVERABLES</div>
        <div class="ky-kpi-val">${num(deliveredCount)}/${num(totalMilestones)}</div>
      </div>
      <div class="ky-kpi-card">
        <div class="ky-kpi-label">SPENT</div>
        <div class="ky-kpi-val">${fmtUsd(spend.usd)}</div>
      </div>
      <div class="ky-kpi-card">
        <div class="ky-kpi-label">REMAINING</div>
        <div class="ky-kpi-val">${remainingTasks > 0 ? `${num(remainingTasks)} tasks` : duration(d.avg_duration_seconds)}</div>
      </div>
    </div>

    <!-- Full-Width CURRENT SPRINT Card (Exact Kyros Overview Layout) -->
    <div class="ky-card">
      <div class="ky-card-head" style="margin-bottom:14px;align-items:flex-start">
        <div style="min-width:0">
          <div class="ky-eyebrow">CURRENT SPRINT</div>
          <h2 class="ky-section-title" style="font-size:17px;margin-top:4px">
            ${selectedMission
              ? `Sprint ${sprintNum} — ${esc(truncate(selectedMission.title, 82))}`
              : 'No Active Sprint'}
          </h2>
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;flex-shrink:0">
          ${milestones.length > 1 ? `
            <select id="ky-sprint-select" class="ky-btn ky-btn-xs" aria-label="Switch Sprint">
              ${milestones.map((m) => `
                <option value="${esc(m.mission_id)}" ${selectedMission && m.mission_id === selectedMission.mission_id ? 'selected' : ''}>
                  Sprint ${m.sprintNumber} · ${esc(truncate(m.title, 36))}
                </option>
              `).join('')}
            </select>
          ` : ''}
          ${selectedMission ? `
            <button type="button" class="ky-btn ky-btn-xs" data-inspect="${esc(selectedMission.mission_id)}">View Diff</button>
            ${selectedMission.files_changed ? `<button type="button" class="ky-btn ky-btn-primary ky-btn-xs" data-apply="${esc(selectedMission.mission_id)}">Apply (${selectedMission.files_changed} files)</button>` : ''}
            <span class="ky-badge ky-badge-done">${sprintDone}/${sprintTotal} done</span>
          ` : ''}
        </div>
      </div>

      ${selectedMission ? `
        <div>
          ${sprintTasks.length ? sprintTasks.map((t) => `
            <div class="ky-task-row" style="cursor:pointer" data-inspect="${esc(selectedMission.mission_id)}">
              <div class="ky-task-left">
                ${taskNodeIcon(t.status)}
                <span class="ky-task-title">${esc(t.title)}</span>
              </div>
              <div style="display:flex;align-items:center;gap:10px;flex-shrink:0">
                ${t.agent ? `<span class="ky-task-agent">${esc(titleCase(t.agent))}</span>` : ''}
                ${taskStatusBadge(t.status)}
              </div>
            </div>
          `).join('') : `<div style="color:var(--text-3);padding:16px 0">No tasks recorded for this sprint yet.</div>`}
        </div>
      ` : `<div style="color:var(--text-3);padding:20px 0">Run a mission via CLI to start tracking sprint tasks.</div>`}
    </div>

    <!-- RECENT DELIVERABLES Section (Exact Kyros Overview Bottom Section) -->
    <div style="margin-top:26px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
        <div class="ky-eyebrow">RECENT DELIVERABLES</div>
        <button type="button" class="ky-btn ky-btn-ghost ky-btn-xs" data-goto-tab="deliverables">View All (${milestones.length}) →</button>
      </div>
      ${recentDeliverables.length ? `
        <div class="ky-deliv-grid">
          ${recentDeliverables.map(renderDeliverableCard).join('')}
        </div>
      ` : `<div class="ky-card" style="color:var(--text-3)">No deliverables recorded yet.</div>`}
    </div>
  `;
}

// ── TAB 2: MILESTONES (Exact Kyros PROJECT TIMELINE) ────────────────────────

function renderMilestonesTab(milestones) {
  // Kyros PROJECT TIMELINE flows chronologically: Sprint 1 -> Sprint N
  const ordered = [...milestones].reverse();

  return `
    <div style="margin-bottom:18px">
      <div class="ky-eyebrow">PROJECT TIMELINE</div>
    </div>

    ${ordered.length ? `
      <div class="ky-timeline-wrap">
        ${ordered.map((m) => {
          const isDone = m.status === 'COMPLETED';
          const isLive = RUNNING.has(m.status);
          const isFail = m.status === 'FAILED' || m.status === 'BLOCKED';
          const nodeCls = isDone ? 'done' : isLive ? 'active' : isFail ? 'failed' : '';
          const pct = isDone ? 100 : (m.progress || (m.tasks_total ? Math.round((m.tasks_done / m.tasks_total) * 100) : 0));
          const tasks = m.tasks || [];

          return `
            <div class="ky-tl-item">
              <div class="ky-tl-node ${nodeCls}">${timelineNodeIcon(isDone, isLive, isFail)}</div>
              <div class="ky-tl-card ${isLive ? 'active' : ''}" style="cursor:pointer" data-inspect="${esc(m.mission_id)}">
                <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
                  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                    <h3 style="margin:0;font-size:16px;font-weight:600">${esc(truncate(m.title, 76))}</h3>
                    ${kyrosMilestoneBadge(m.status)}
                  </div>
                  <div style="font-family:var(--font-mono);font-size:12px;color:var(--text-3)">
                    Sprint ${m.sprintNumber}${m.created_at ? ` · ${timeAgo(m.created_at)}` : ''}
                  </div>
                </div>

                ${!isDone ? `
                  <div style="margin-top:14px">
                    <div style="display:flex;justify-content:space-between;font-family:var(--font-mono);font-size:11.5px;color:var(--text-3);margin-bottom:6px">
                      <span>Progress</span>
                      <span style="color:var(--text-1);font-weight:600">${pct}%</span>
                    </div>
                    <div class="ky-master-bar" style="margin-bottom:0;height:5px">
                      <span style="width:${pct}%"></span>
                    </div>
                  </div>
                ` : ''}

                <!-- Task Deliverable Chips (Exact Kyros Timeline Chips) -->
                <div class="ky-chip-cloud">
                  ${tasks.map((t) => {
                    const tc = t.status === 'COMPLETED' ? 'done' : t.status === 'FAILED' ? 'failed' : '';
                    return `<span class="ky-task-chip ${tc}">${esc(t.title)}</span>`;
                  }).join('')}
                </div>

                <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;font-family:var(--font-mono);font-size:12px;color:var(--text-3)">
                  <div>
                    <span>${tasks.length} deliverables</span>
                    ${m.files_changed ? ` · <span>${m.files_changed} files (+${num(m.lines_added || 0)} LOC)</span>` : ''}
                  </div>
                  <div style="display:flex;gap:8px" data-stop-prop="1">
                    <button type="button" class="ky-btn ky-btn-xs" data-inspect="${esc(m.mission_id)}">View Diff</button>
                    ${m.files_changed ? `<button type="button" class="ky-btn ky-btn-primary ky-btn-xs" data-apply="${esc(m.mission_id)}">Apply to Repo</button>` : ''}
                    ${isFail ? `<button type="button" class="ky-btn ky-btn-xs" data-retry="${esc(m.mission_id)}">Retry</button>` : ''}
                  </div>
                </div>
              </div>
            </div>
          `;
        }).join('')}
      </div>
    ` : `<div class="ky-card" style="color:var(--text-3);text-align:center;padding:36px">No milestones recorded yet.</div>`}
  `;
}

// ── TAB 3: DELIVERABLES (Exact Kyros 2-Column Grid) ─────────────────────────

function renderDeliverableCard(m) {
  const taskPct = m.tasks_total
    ? Math.round((m.tasks_done / m.tasks_total) * 100)
    : (m.status === 'COMPLETED' ? 100 : 0);
  const hasFiles = m.files_changed != null && m.files_changed > 0;
  const locTotal = (Number(m.lines_added) || 0) + (Number(m.lines_removed) || 0);

  return `
    <div class="ky-deliv-card" style="cursor:pointer" data-inspect="${esc(m.mission_id)}">
      <div>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px">
          <div style="display:flex;align-items:center;gap:9px;min-width:0">
            ${deliverableIcon(m.status)}
            <h4 style="margin:0;font-size:15px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(m.title)}">
              ${esc(truncate(m.title, 52))}
            </h4>
          </div>
          ${kyrosDeliverableBadge(m.status)}
        </div>

        <div class="ky-deliv-metrics">
          <span>${m.files_changed != null ? `${m.files_changed} files` : '0 files'}</span>
          <span>${locTotal > 0 ? `${num(locTotal)} LOC` : '0 LOC'}</span>
          <span>${taskPct}% verified</span>
        </div>
      </div>

      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;font-family:var(--font-mono);font-size:12px;color:var(--text-3)">
        <span>Sprint ${m.sprintNumber || 1}${m.branch ? ` · ${esc(m.branch)}` : ''}</span>
        <div style="display:flex;gap:6px" data-stop-prop="1">
          <button type="button" class="ky-btn ky-btn-xs" data-inspect="${esc(m.mission_id)}">View Diff</button>
          ${hasFiles ? `<button type="button" class="ky-btn ky-btn-primary ky-btn-xs" data-apply="${esc(m.mission_id)}">Apply</button>` : ''}
        </div>
      </div>
    </div>
  `;
}

function renderDeliverablesTab(milestones) {
  const filtered = milestones.filter((m) => {
    if (state.delivFilter === 'delivered') return m.status === 'COMPLETED' || (m.files_changed && m.files_changed > 0);
    if (state.delivFilter === 'progress') return RUNNING.has(m.status);
    if (state.delivFilter === 'upcoming') return m.status === 'FAILED' || m.status === 'BLOCKED' || m.status === 'AWAITING_APPROVAL' || m.status === 'DRAFT';
    return true;
  });

  return `
    <div class="ky-filter-bar">
      <div class="ky-filter-pills" id="ky-deliv-filters">
        <button type="button" class="ky-filter-pill ${state.delivFilter === 'all' ? 'active' : ''}" data-df="all">All</button>
        <button type="button" class="ky-filter-pill ${state.delivFilter === 'delivered' ? 'active' : ''}" data-df="delivered">Delivered</button>
        <button type="button" class="ky-filter-pill ${state.delivFilter === 'progress' ? 'active' : ''}" data-df="progress">In Progress</button>
        <button type="button" class="ky-filter-pill ${state.delivFilter === 'upcoming' ? 'active' : ''}" data-df="upcoming">Upcoming</button>
      </div>
      <div style="font-family:var(--font-mono);font-size:12px;color:var(--text-3)">${filtered.length} items</div>
    </div>

    ${filtered.length ? `
      <div class="ky-deliv-grid">
        ${filtered.map(renderDeliverableCard).join('')}
      </div>
    ` : `<div class="ky-card" style="color:var(--text-3);text-align:center;padding:36px">No deliverables match this filter.</div>`}
  `;
}

// ── TAB 4: TEAM (21 Autonomous Engineering Specialists) ─────────────────────

function renderTeamTab(d) {
  const roster = d.roster || [];
  const filtered = roster.filter((a) => {
    if (state.teamFilter === 'active' && !a.runs) return false;
    if (state.teamQuery) {
      const q = state.teamQuery.toLowerCase();
      return (a.name || '').toLowerCase().includes(q)
        || (a.type || '').toLowerCase().includes(q)
        || (a.description || '').toLowerCase().includes(q);
    }
    return true;
  });

  return `
    <div class="ky-filter-bar">
      <div class="ky-filter-pills" id="ky-team-filters">
        <button type="button" class="ky-filter-pill ${state.teamFilter === 'all' ? 'active' : ''}" data-tf="all">All Specialists (${roster.length})</button>
        <button type="button" class="ky-filter-pill ${state.teamFilter === 'active' ? 'active' : ''}" data-tf="active">Active on Project (${roster.filter((a) => a.runs > 0).length})</button>
      </div>
      <input type="search" id="ky-team-search" value="${esc(state.teamQuery)}" placeholder="Filter specialist or capability…" style="background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:6px 12px;font-size:12.5px;width:240px" />
    </div>

    <div class="ky-team-grid">
      ${filtered.map((a) => `
        <div class="ky-agent-card" style="cursor:pointer" data-agent-inspect="${esc(a.type)}">
          <div>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px">
              <div style="display:flex;align-items:center;gap:10px">
                <span class="ky-avatar ${a.runs ? '' : 'muted'}">${esc((a.name || 'A').slice(0, 2).toUpperCase())}</span>
                <div>
                  <div style="font-weight:700;font-size:14.5px">${esc(a.name)}</div>
                  <div style="font-family:var(--font-mono);font-size:10.5px;color:var(--text-3)">${esc(titleCase(a.type))}</div>
                </div>
              </div>
              <span class="ky-badge ${a.runs ? 'ky-badge-done' : ''}">${a.runs ? `${num(a.runs)} runs` : 'Standby'}</span>
            </div>
            <p style="margin:0 0 10px;color:var(--text-2);font-size:12.5px">${esc(a.description || '')}</p>
            <div style="display:flex;flex-wrap:wrap;gap:5px">
              ${(a.capabilities || []).slice(0, 4).map((c) => `<span class="ky-task-agent">${esc(c)}</span>`).join('')}
            </div>
          </div>
          <div class="ky-deliv-foot">
            <span>Pass: ${a.success_rate != null ? `${a.success_rate}%` : DASH}</span>
            <span>Tokens: ${a.tokens ? compact(a.tokens) : DASH}</span>
            <span>${a.last_run_at ? timeAgo(a.last_run_at) : 'Idle'}</span>
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

// ── TAB 5: REPORTS (Real Engineering Telemetry & Analytics) ─────────────────

function renderReportsTab(d, milestones) {
  const pb = d.phase_breakdown || {};
  const activeAgents = (d.roster || []).filter((a) => a.runs > 0);
  const totalLocAdded = milestones.reduce((acc, m) => acc + (Number(m.lines_added) || 0), 0);
  const totalFiles = milestones.reduce((acc, m) => acc + (Number(m.files_changed) || 0), 0);

  return `
    <div class="ky-kpi-grid">
      <div class="ky-kpi-card">
        <div class="ky-kpi-label">SANDBOX LOC ADDED</div>
        <div class="ky-kpi-val">+${num(totalLocAdded)}</div>
        <div class="ky-kpi-sub">Across ${num(totalFiles)} modified files</div>
      </div>
      <div class="ky-kpi-card">
        <div class="ky-kpi-label">AVG MISSION DURATION</div>
        <div class="ky-kpi-val">${duration(d.avg_duration_seconds)}</div>
        <div class="ky-kpi-sub">End-to-end autonomous cycle</div>
      </div>
      <div class="ky-kpi-card">
        <div class="ky-kpi-label">INPUT / OUTPUT TOKENS</div>
        <div class="ky-kpi-val">${compact(d.spend?.tokens_input)} / ${compact(d.spend?.tokens_output)}</div>
        <div class="ky-kpi-sub">Total spend: ${fmtUsd(d.spend?.usd)}</div>
      </div>
      <div class="ky-kpi-card">
        <div class="ky-kpi-label">TASK PASS RATE</div>
        <div class="ky-kpi-val">${d.progress?.percent != null ? `${d.progress.percent}%` : DASH}</div>
        <div class="ky-kpi-sub">${num(d.progress?.completed_tasks)} of ${num(d.progress?.total_tasks)} tasks verified</div>
      </div>
    </div>

    <div class="ky-two-col">
      <div class="ky-card">
        <div class="ky-eyebrow" style="margin-bottom:14px">11-PHASE PIPELINE THROUGHPUT</div>
        ${PHASES.map(([key, label]) => {
          const row = pb[key] || { total: 0, completed: 0, failed: 0 };
          const pct = row.total ? Math.round((row.completed / row.total) * 100) : 0;
          return `
            <div style="margin-bottom:12px">
              <div style="display:flex;justify-content:space-between;font-family:var(--font-mono);font-size:11.5px;margin-bottom:4px">
                <span>${esc(label)} <span style="color:var(--text-3)">(${key})</span></span>
                <span>${row.completed}/${row.total} passed ${row.failed ? `· ${row.failed} failed` : ''}</span>
              </div>
              <div class="ky-master-bar" style="height:4px;margin-bottom:0">
                <span style="width:${pct}%"></span>
              </div>
            </div>
          `;
        }).join('')}
      </div>

      <div class="ky-card">
        <div class="ky-eyebrow" style="margin-bottom:14px">SPECIALIST WORKLOAD &amp; COST LEDGER</div>
        ${activeAgents.length ? activeAgents.map((a) => `
          <div class="ky-task-row" style="cursor:pointer" data-agent-inspect="${esc(a.type)}">
            <div>
              <div style="font-weight:600">${esc(a.name)} <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-3)">(${esc(titleCase(a.type))})</span></div>
              <div style="font-family:var(--font-mono);font-size:11px;color:var(--text-3)">
                Pass rate: ${a.success_rate != null ? `${a.success_rate}%` : DASH} · Tokens: ${compact(a.tokens)}
              </div>
            </div>
            <div style="text-align:right;font-family:var(--font-mono);font-size:12px">
              <div style="font-weight:700">${num(a.runs)} runs</div>
              <div style="color:var(--text-3)">${a.cost_usd != null ? fmtUsd(a.cost_usd) : DASH}</div>
            </div>
          </div>
        `).join('') : `<div style="color:var(--text-3);padding:16px 0">No agent telemetry recorded for this project yet.</div>`}
      </div>
    </div>
  `;
}

// ── Event Wiring ────────────────────────────────────────────────────────────

function wireTabEvents() {
  document.querySelectorAll('.ky-tab[data-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.tab = btn.dataset.tab;
      location.hash = `#${state.tab}`;
      renderDashboard();
    });
  });

  document.querySelectorAll('[data-goto-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.tab = btn.dataset.gotoTab;
      location.hash = `#${state.tab}`;
      renderDashboard();
    });
  });

  const sprintSel = document.getElementById('ky-sprint-select');
  if (sprintSel) {
    sprintSel.addEventListener('change', () => {
      state.selectedMissionId = sprintSel.value;
      renderDashboard();
    });
  }

  document.querySelectorAll('#ky-deliv-filters button[data-df]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.delivFilter = btn.dataset.df;
      renderDashboard();
    });
  });

  document.querySelectorAll('#ky-team-filters button[data-tf]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.teamFilter = btn.dataset.tf;
      renderDashboard();
    });
  });

  const teamSearch = document.getElementById('ky-team-search');
  if (teamSearch) {
    teamSearch.addEventListener('input', () => {
      state.teamQuery = teamSearch.value;
      renderDashboard();
      const input = document.getElementById('ky-team-search');
      if (input) {
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      }
    });
  }

  // Stop propagation on button containers inside clickable cards
  document.querySelectorAll('[data-stop-prop]').forEach((el) => {
    el.addEventListener('click', (e) => e.stopPropagation());
  });

  // Specialist Inspector triggers
  document.querySelectorAll('[data-agent-inspect]').forEach((el) => {
    el.addEventListener('click', () => openSpecialistInspector(el.dataset.agentInspect));
  });

  // Deliverable / Diff / Apply / Retry triggers
  document.querySelectorAll('[data-inspect]').forEach((el) => {
    el.addEventListener('click', () => openMissionInspector(el.dataset.inspect));
  });
  document.querySelectorAll('[data-apply]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openMissionInspector(btn.dataset.apply, true);
    });
  });
  document.querySelectorAll('[data-retry]').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const mid = btn.dataset.retry;
      btn.disabled = true;
      try {
        await post(`/missions/${encodeURIComponent(mid)}/retry`, {});
        notify('Mission retry started');
        await fetchDashboard(true);
      } catch (err) {
        notify(err.message, 'error');
        btn.disabled = false;
      }
    });
  });
}

// ── Specialist Inspector Modal (Team Tab Interactive View) ──────────────────

function openSpecialistInspector(agentType) {
  const d = state.dashboard;
  const agent = (d?.roster || []).find((a) => a.type === agentType);
  if (!agent) return;

  // Gather all tasks across project milestones assigned to this specialist
  const rawMilestones = d?.milestones || [];
  const totalMissions = rawMilestones.length;
  const assignedTasks = [];
  rawMilestones.forEach((m, idx) => {
    const sprintNum = totalMissions - idx;
    (m.tasks || []).forEach((t) => {
      if (t.agent === agent.type || t.agent === agent.name) {
        assignedTasks.push({
          ...t,
          mission_id: m.mission_id,
          mission_title: m.title,
          sprintNumber: sprintNum,
        });
      }
    });
  });

  const host = document.getElementById('ky-modal-host');
  host.innerHTML = `
    <div class="ky-modal-overlay" id="ky-modal-bg">
      <div class="ky-modal" style="max-width:740px" role="dialog" aria-modal="true">
        <div class="ky-modal-head">
          <div style="display:flex;align-items:center;gap:12px">
            <span class="ky-avatar">${esc((agent.name || 'A').slice(0, 2).toUpperCase())}</span>
            <div>
              <div class="ky-eyebrow">${esc(titleCase(agent.type))}</div>
              <h3 style="margin:2px 0 0;font-size:18px">${esc(agent.name)}</h3>
            </div>
          </div>
          <button type="button" class="ky-icon-btn" id="ky-modal-close">✕</button>
        </div>
        <div class="ky-modal-body">
          <p style="margin:0 0 16px;color:var(--text-2)">${esc(agent.description || '')}</p>

          <div class="ky-kpi-grid" style="margin-bottom:18px">
            <div class="ky-kpi-card" style="padding:14px">
              <div class="ky-kpi-label">PROJECT RUNS</div>
              <div class="ky-kpi-val" style="font-size:22px">${num(agent.runs)}</div>
            </div>
            <div class="ky-kpi-card" style="padding:14px">
              <div class="ky-kpi-label">PASS RATE</div>
              <div class="ky-kpi-val" style="font-size:22px">${agent.success_rate != null ? `${agent.success_rate}%` : DASH}</div>
            </div>
            <div class="ky-kpi-card" style="padding:14px">
              <div class="ky-kpi-label">TOKENS USED</div>
              <div class="ky-kpi-val" style="font-size:22px">${agent.tokens ? compact(agent.tokens) : DASH}</div>
            </div>
            <div class="ky-kpi-card" style="padding:14px">
              <div class="ky-kpi-label">EST. COST</div>
              <div class="ky-kpi-val" style="font-size:22px">${agent.cost_usd != null ? fmtUsd(agent.cost_usd) : DASH}</div>
            </div>
          </div>

          <div class="ky-eyebrow" style="margin-bottom:10px">ASSIGNED SPRINT DELIVERABLES (${assignedTasks.length})</div>
          ${assignedTasks.length ? assignedTasks.map((t) => `
            <div class="ky-task-row" style="cursor:pointer" data-open-mission="${esc(t.mission_id)}">
              <div class="ky-task-left">
                ${taskNodeIcon(t.status)}
                <div>
                  <div class="ky-task-title">${esc(t.title)}</div>
                  <div style="font-family:var(--font-mono);font-size:11px;color:var(--text-3)">Sprint ${t.sprintNumber} · ${esc(truncate(t.mission_title, 54))}</div>
                </div>
              </div>
              ${taskStatusBadge(t.status)}
            </div>
          `).join('') : `<div style="color:var(--text-3);padding:12px 0">No sprint tasks assigned to this specialist yet.</div>`}
        </div>
      </div>
    </div>
  `;

  const close = () => { host.innerHTML = ''; };
  document.getElementById('ky-modal-close')?.addEventListener('click', close);
  document.getElementById('ky-modal-bg')?.addEventListener('click', (e) => {
    if (e.target.id === 'ky-modal-bg') close();
  });
  host.querySelectorAll('[data-open-mission]').forEach((row) => {
    row.addEventListener('click', () => openMissionInspector(row.dataset.openMission));
  });
}

// ── Deliverable & Sandbox Diff Inspector Modal ──────────────────────────────

async function openMissionInspector(missionId, autoFocusApply = false) {
  const host = document.getElementById('ky-modal-host');
  host.innerHTML = `
    <div class="ky-modal-overlay" id="ky-modal-bg">
      <div class="ky-modal" role="dialog" aria-modal="true">
        <div class="ky-modal-head">
          <div>
            <div class="ky-eyebrow">DELIVERABLE &amp; SANDBOX INSPECTOR · ${esc(missionId.slice(0, 8))}</div>
            <h3 style="margin:4px 0 0;font-size:17px" id="ky-insp-title">Loading deliverable…</h3>
          </div>
          <button type="button" class="ky-icon-btn" id="ky-modal-close">✕</button>
        </div>
        <div class="ky-modal-body" id="ky-insp-body">
          <div style="color:var(--text-2);font-family:var(--font-mono)">Inspecting sandbox git diff and task verification…</div>
        </div>
      </div>
    </div>
  `;

  const close = () => { host.innerHTML = ''; };
  document.getElementById('ky-modal-close')?.addEventListener('click', close);
  document.getElementById('ky-modal-bg')?.addEventListener('click', (e) => {
    if (e.target.id === 'ky-modal-bg') close();
  });

  try {
    let [m, diffData, activity] = await Promise.all([
      get(`/missions/${encodeURIComponent(missionId)}`),
      get(`/missions/${encodeURIComponent(missionId)}/diff`).catch(() => ({ files: [], totals: { files: 0, added: 0, removed: 0 } })),
      get(`/missions/${encodeURIComponent(missionId)}/activity`).catch(() => ({ events: [] })),
    ]);

    const titleEl = document.getElementById('ky-insp-title');
    const bodyEl = document.getElementById('ky-insp-body');
    if (!titleEl || !bodyEl) return;

    titleEl.textContent = m.title || missionId;
    const files = diffData?.files || [];
    let selectedFileIdx = 0;

    const renderBody = (applyResultHtml = '') => {
      const activeFile = files[selectedFileIdx] || null;
      const events = Array.isArray(activity) ? activity : (activity?.events || []);
      const canApprove = m.status === 'AWAITING_APPROVAL' || m.approval_status === 'PENDING';
      const canRetry = ['FAILED', 'BLOCKED', 'CANCELLED'].includes(m.status);
      const canCancel = ['ACTIVE', 'PLANNING', 'EXECUTING', 'READY', 'AWAITING_APPROVAL', 'BLOCKED'].includes(m.status);
      const prInfo = m.delivery?.pr || null;

      bodyEl.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            ${kyrosDeliverableBadge(m.status)}
            <span class="ky-task-agent">PHASE: ${esc(m.phase || 'INTAKE')}</span>
            <span class="ky-task-agent">${esc(diffData.branch || m.active_branch || '')}</span>
            ${prInfo ? `<a href="${esc(prInfo.url)}" target="_blank" rel="noopener" class="ky-badge ky-badge-ok" style="text-decoration:none">PR #${num(prInfo.number)} · ${esc(prInfo.status || 'OPEN')} ↗</a>` : ''}
            ${diffData.sandbox_dir ? `<span class="ky-task-agent">${esc(diffData.sandbox_dir)}</span>` : ''}
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            ${canApprove ? `
              <button type="button" class="ky-btn ky-btn-primary ky-btn-xs" id="ky-act-approve">Approve Plan</button>
              <button type="button" class="ky-btn ky-btn-xs" id="ky-act-reject" style="color:var(--err-fg)">Reject</button>
            ` : ''}
            ${canRetry ? `<button type="button" class="ky-btn ky-btn-xs" id="ky-act-retry">Retry Sprint</button>` : ''}
            ${canCancel ? `<button type="button" class="ky-btn ky-btn-xs" id="ky-act-cancel">Cancel Sprint</button>` : ''}
            ${files.length ? `
              <button type="button" class="ky-btn ky-btn-xs" id="ky-act-deliver">${prInfo ? `Sync PR #${num(prInfo.number)}` : 'Push &amp; Open PR'}</button>
              <button type="button" class="ky-btn ky-btn-xs" id="ky-act-dryrun">Dry-Run Check</button>
              <button type="button" class="ky-btn ky-btn-primary ky-btn-xs" id="ky-act-apply">Apply ${files.length} File(s) to Repo</button>
            ` : ''}
          </div>
        </div>

        ${renderPhaseStrip(m.phase, m.status)}

        <!-- Live Mid-Flight Steering Bar -->
        <div style="display:flex;gap:8px;align-items:center;margin:12px 0">
          <input type="text" id="ky-steer-input" class="ky-select" style="flex:1;height:32px;padding:0 10px;font-family:var(--font-mono);font-size:12px" placeholder="Steer sprint mid-flight (e.g. 'Use stdlib only, skip external deps')…" />
          <button type="button" class="ky-btn ky-btn-xs" id="ky-act-steer">Steer Sprint</button>
        </div>

        ${applyResultHtml}

        <!-- Sandbox Unified Diff Viewer -->
        <div style="margin-top:14px;margin-bottom:18px">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;flex-wrap:wrap;gap:8px">
            <div class="ky-eyebrow">CODE CHANGES (${files.length} FILES · +${num(diffData.totals?.added || 0)} / -${num(diffData.totals?.removed || 0)} LOC)</div>
            <div style="display:flex;gap:6px;flex-wrap:wrap">
              ${files.map((f, idx) => `
                <button type="button" class="ky-btn ky-btn-xs ${idx === selectedFileIdx ? 'ky-btn-primary' : ''}" data-file-idx="${idx}">
                  ${esc(f.path)} (+${f.added}/-${f.removed})
                </button>
              `).join('')}
            </div>
          </div>
          ${activeFile
            ? renderDiff(activeFile.diff).replace('class="diff"', 'class="ky-diff-wrap"').replace('<table>', '<table class="ky-diff-table">')
            : `<div style="color:var(--text-3);padding:12px 0;font-family:var(--font-mono);font-size:12px">No modified files in this sandbox.</div>`}
        </div>

        <!-- Tasks & Agent Execution Log -->
        <div class="ky-two-col">
          <div>
            <div class="ky-eyebrow" style="margin-bottom:8px">SPRINT TASKS (${(m.tasks || []).length})</div>
            ${(m.tasks || []).map((t) => `
              <div class="ky-task-row">
                <div class="ky-task-left">
                  ${taskNodeIcon(t.status)}
                  <span class="ky-task-title">${esc(t.title)}</span>
                </div>
                <div style="display:flex;gap:6px;align-items:center">
                  ${t.agent_role ? `<span class="ky-task-agent">${esc(titleCase(t.agent_role))}</span>` : ''}
                  ${taskStatusBadge(t.status)}
                </div>
              </div>
            `).join('')}
          </div>
          <div>
            <div class="ky-eyebrow" style="margin-bottom:8px">EXECUTION LOG (${events.length})</div>
            ${events.length ? events.slice(0, 8).map((a) => `
              <div class="ky-task-row">
                <div style="min-width:0">
                  <div style="font-weight:600;font-size:12.5px">${esc(a.agent_name || titleCase(a.agent_role || 'Specialist'))} · ${esc(a.task_title || '')}</div>
                  <div style="font-size:11.5px;color:var(--text-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(a.summary || a.error || a.status || '')}</div>
                </div>
                <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-3)">${duration(a.duration_seconds)}</span>
              </div>
            `).join('') : `<div style="color:var(--text-3);font-size:12px">No execution logs recorded.</div>`}
          </div>
        </div>
      `;

      bodyEl.querySelectorAll('[data-file-idx]').forEach((b) => {
        b.addEventListener('click', () => {
          selectedFileIdx = Number(b.dataset.fileIdx);
          renderBody(applyResultHtml);
        });
      });

      document.getElementById('ky-act-deliver')?.addEventListener('click', async () => {
        try {
          const delRes = await post(`/missions/${encodeURIComponent(missionId)}/deliver`, { push_and_open_pr: true });
          const prLabel = delRes.pr ? `PR #${delRes.pr.number} (${delRes.pr.url})` : 'Changeset persisted';
          notify(`Deliverable ready: ${prLabel}`);
          m = await get(`/missions/${encodeURIComponent(missionId)}`);
          renderBody(applyResultHtml);
          await fetchDashboard(true);
        } catch (err) {
          notify(err.message || 'Deliver failed', 'error');
        }
      });
      document.getElementById('ky-act-dryrun')?.addEventListener('click', () => runApply(true));
      document.getElementById('ky-act-apply')?.addEventListener('click', () => runApply(false));
      document.getElementById('ky-act-retry')?.addEventListener('click', async () => {
        try {
          await post(`/missions/${encodeURIComponent(missionId)}/retry`, {});
          notify('Sprint retry triggered');
          close();
          await fetchDashboard(true);
        } catch (err) {
          notify(err.message || 'Retry failed', 'error');
        }
      });
      document.getElementById('ky-act-approve')?.addEventListener('click', async () => {
        try {
          await post(`/missions/${encodeURIComponent(missionId)}/approve`, {});
          notify('Sprint approved');
          close();
          await fetchDashboard(true);
        } catch (err) {
          notify(err.message || 'Approve failed', 'error');
        }
      });
      document.getElementById('ky-act-reject')?.addEventListener('click', async () => {
        try {
          const reason = document.getElementById('ky-steer-input')?.value?.trim() || 'Rejected from Web Inspector';
          await post(`/missions/${encodeURIComponent(missionId)}/reject`, { reason });
          notify('Sprint rejected');
          close();
          await fetchDashboard(true);
        } catch (err) {
          notify(err.message || 'Reject failed', 'error');
        }
      });
      document.getElementById('ky-act-cancel')?.addEventListener('click', async () => {
        try {
          await post(`/missions/${encodeURIComponent(missionId)}/cancel`, {});
          notify('Sprint cancelled');
          close();
          await fetchDashboard(true);
        } catch (err) {
          notify(err.message || 'Cancel failed', 'error');
        }
      });

      const submitSteer = async () => {
        const inputEl = document.getElementById('ky-steer-input');
        const instruction = inputEl?.value?.trim();
        if (!instruction) {
          notify('Enter a steering instruction first', 'error');
          return;
        }
        try {
          await post(`/missions/${encodeURIComponent(missionId)}/steer`, { instruction });
          notify('Steering directive queued for active sprint');
          m = await get(`/missions/${encodeURIComponent(missionId)}`);
          activity = await get(`/missions/${encodeURIComponent(missionId)}/activity`).catch(() => ({ events: [] }));
          renderBody(applyResultHtml);
          await fetchDashboard(true);
        } catch (err) {
          notify(err.message || 'Steer failed', 'error');
        }
      };

      document.getElementById('ky-act-steer')?.addEventListener('click', submitSteer);
      document.getElementById('ky-steer-input')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          submitSteer();
        }
      });
    };

    const runApply = async (dryRun) => {
      try {
        const res = await post(`/missions/${encodeURIComponent(missionId)}/apply`, { dry_run: dryRun, force: false });
        const appliedCount = res.applied_count ?? (res.applied || []).length;
        const skippedCount = res.skipped_count ?? (res.skipped || []).length;
        const conflictCount = res.conflict_count ?? (res.conflicts || []).length;
        const banner = `
          <div class="ky-card" style="margin:10px 0;border-color:var(--accent-border);background:var(--bg-elevated)">
            <div class="ky-eyebrow">${dryRun ? 'DRY-RUN MERGE PREVIEW' : 'SANDBOX APPLIED TO WORKSPACE'} · DONE</div>
            <div style="font-family:var(--font-mono);font-size:12px;margin-top:6px">
              Applied: ${appliedCount} · Unchanged/Skipped: ${skippedCount} · Conflicts: ${conflictCount}
              ${res.workspace_dir ? `<div style="color:var(--text-3);margin-top:4px">Target: ${esc(res.workspace_dir)}</div>` : ''}
            </div>
          </div>
        `;
        notify(dryRun ? `Dry-run: ${appliedCount} file(s) ready to apply` : `Applied ${appliedCount} file(s) to workspace`);
        renderBody(banner);
        if (!dryRun) await fetchDashboard(true);
      } catch (err) {
        notify(err.message || 'Apply failed', 'error');
      }
    };

    renderBody();
    if (autoFocusApply && files.length) {
      await runApply(true);
    }
  } catch (err) {
    const bodyEl = document.getElementById('ky-insp-body');
    if (bodyEl) bodyEl.innerHTML = `<div style="color:var(--err-fg)">${esc(err.message)}</div>`;
  }
}

// ── Top Bar Initialization ──────────────────────────────────────────────────

function initTopBar() {
  const projSel = document.getElementById('ky-project-select');
  projSel?.addEventListener('change', () => {
    state.projectId = projSel.value;
    state.selectedMissionId = null;
    localStorage.setItem('kobits.projectId', state.projectId);
    fetchDashboard();
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.getElementById('ky-modal-host').innerHTML = '';
    }
  });

  window.addEventListener('hashchange', () => {
    const h = location.hash.replace(/^#\/?/, '');
    if (['overview', 'milestones', 'deliverables', 'team', 'reports'].includes(h)) {
      state.tab = h;
      renderDashboard();
    }
  });
}

// ── Live WebSocket Telemetry Stream (<100ms Push Updates) ───────────────────

let _wsDebounceTimer = null;
function scheduleInstantRefresh() {
  if (_wsDebounceTimer) clearTimeout(_wsDebounceTimer);
  _wsDebounceTimer = setTimeout(() => {
    fetchDashboard(true);
  }, 60);
}

function initLiveWebSocket() {
  if (typeof WebSocket === 'undefined' || !location.host) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${proto}//${location.host}/ws/dashboard`;
  let ws = null;
  let pingTimer = null;

  const connect = () => {
    try {
      ws = new WebSocket(wsUrl);
      ws.addEventListener('open', () => {
        if (pingTimer) clearInterval(pingTimer);
        pingTimer = setInterval(() => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send('ping');
          }
        }, 25000);
      });
      ws.addEventListener('message', (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (!msg || msg.type === 'system' || msg.type === 'pong') return;
          scheduleInstantRefresh();
        } catch {
          // ignore malformed frames
        }
      });
      ws.addEventListener('close', () => {
        if (pingTimer) clearInterval(pingTimer);
        setTimeout(connect, 2500);
      });
      ws.addEventListener('error', () => {
        try { ws.close(); } catch {}
      });
    } catch {
      setTimeout(connect, 4000);
    }
  };

  connect();
}

// ── Bootstrap ───────────────────────────────────────────────────────────────

const initialHash = location.hash.replace(/^#\/?/, '');
if (['overview', 'milestones', 'deliverables', 'team', 'reports'].includes(initialHash)) {
  state.tab = initialHash;
}

initTopBar();
fetchDashboard();
initLiveWebSocket();
setInterval(() => {
  fetchDashboard(true);
}, 8000);
