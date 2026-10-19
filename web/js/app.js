// ============================================================================
// KOBITS — AUTONOMOUS MONOCHROME ENGINEERING DASHBOARD
// 100% Real Database Telemetry (/api/v1/org/dashboard, /missions/{id}/diff, /apply)
// ============================================================================

import { get, post } from './api.js';
import {
  DASH, duration, esc, num, compact, PHASES, phaseIndex,
  renderDiff, RUNNING, timeAgo, titleCase, truncate,
} from './ui.js';
import { agentGlyph } from './agent-glyph.js';

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

// ── 3-Tier Agency Dashboard Surface Routing (#portal, #admin, #console) ─────

function getEffectiveTriadRoute() {
  const rawHash = (location.hash || '').replace(/^#\/?/, '').split('?')[0];
  if (rawHash) return rawHash;
  const path = (location.pathname || '').replace(/^\/+|\/+$/g, '');
  if (path === 'portal' || path === 'client-portal' || path === 'portal-summary' || path === 'teamwork-preview' || path === 'teamwork' || path === 'boost') return path;
  if (path === 'admin' || path === 'agency-admin') return 'admin';
  if (path === 'console' || path.startsWith('console/')) return 'console';
  return '';
}

function isTriadSurfaceRoute(routeStr) {
  const h = (routeStr !== undefined ? routeStr : getEffectiveTriadRoute()).split('?')[0];
  return (
    h === 'portal' ||
    h === 'client-portal' ||
    h === 'portal-summary' ||
    h.startsWith('portal/') ||
    h === 'teamwork-preview' ||
    h === 'teamwork' ||
    h === 'boost' ||
    h === 'team' ||
    h === 'reports' ||
    h === 'milestones' ||
    h === 'deliverables' ||
    h === 'admin' ||
    h === 'agency-admin' ||
    h === 'console' ||
    h.startsWith('console/')
  );
}

function syncTriadNavBar(activeSurface, explicitRole, orgData) {
  let role = explicitRole || window.__kobitsActiveOrgRole || '';
  if (!role) {
    try {
      const savedUser = JSON.parse(localStorage.getItem('kobits_user') || '{}');
      role = savedUser.role || (savedUser.user && savedUser.user.role) || '';
    } catch (_) {}
  }
  const normRole = String(role || '').toUpperCase();
  const token = localStorage.getItem('kobits_token');
  const isGuestPreview = Boolean(window.__kobitsIsGuestPreview);
  const hasAuth = Boolean(token) && !isGuestPreview;
  const isOperator = hasAuth && (normRole === 'OWNER' || normRole === 'ADMIN' || normRole === 'LEAD_ENGINEER' || normRole === 'AGENCY_ADMIN');
  const isClientPortal = activeSurface === 'portal';

  // Surface switcher & developer control buttons
  const operatorControls = document.getElementById('ky-operator-controls');
  const surfaceSwitcher = document.getElementById('ky-surface-switcher');
  const navPortal = document.getElementById('ky-nav-portal');
  const navAdmin = document.getElementById('ky-nav-admin');
  const navConsole = document.getElementById('ky-nav-console');
  const btnNewProject = document.getElementById('ky-topbar-new-project');
  const btnTour = document.getElementById('ky-topbar-tour');
  const btnNewMission = document.getElementById('ky-topbar-new');
  const btnGitHub = document.getElementById('ky-topbar-github');
  const btnSettings = document.getElementById('ky-topbar-settings');

  // Dedicated Client Topbar elements
  const projectPill = document.getElementById('ky-project-pill');
  const clientBrandLabel = document.getElementById('ky-client-brand-label');
  const clientBrandName = document.getElementById('ky-client-brand-name');
  const clientExecPill = document.getElementById('ky-client-exec-pill');
  const clientTopbarActions = document.getElementById('ky-client-topbar-actions');
  const clientLoginBtn = document.getElementById('ky-client-operator-login');
  const brandLink = document.querySelector('.ky-brand');

  const clientDisplayName = (orgData && orgData.project && orgData.project.name)
    || (orgData && orgData.organization && orgData.organization.name)
    || window.__kobitsActiveClientBrand
    || 'Enterprise Core Platform';

  if (isClientPortal && !isOperator) {
    // ── DEDICATED EXECUTIVE CLIENT TOPBAR ──
    // Decoupled from internal developer tools: hide switcher tabs and dev buttons completely!
    if (operatorControls) operatorControls.style.display = 'none';
    if (surfaceSwitcher) surfaceSwitcher.style.display = 'none';
    if (btnNewProject) btnNewProject.style.display = 'none';
    if (btnTour) btnTour.style.display = 'none';
    if (btnNewMission) btnNewMission.style.display = 'none';
    if (btnGitHub) btnGitHub.style.display = 'none';
    if (btnSettings) btnSettings.style.display = 'none';

    // Replace project selector dropdown with clean read-only client branding
    if (projectPill) projectPill.style.display = 'none';
    if (clientBrandLabel) {
      clientBrandLabel.style.display = 'inline-flex';
      if (clientBrandName) clientBrandName.textContent = clientDisplayName;
    }

    // Surface clean executive branding and login/sign-out action
    if (clientExecPill) clientExecPill.style.display = 'inline-flex';
    if (clientTopbarActions) {
      clientTopbarActions.style.display = 'inline-flex';
      if (clientLoginBtn) {
        if (hasAuth) {
          clientLoginBtn.textContent = 'Account / Sign Out';
          clientLoginBtn.href = '/portal.html?logout=1#login';
        } else {
          clientLoginBtn.textContent = 'Sign In / Operator Login';
          clientLoginBtn.href = '/portal.html#login';
        }
      }
    }
    if (brandLink) brandLink.setAttribute('href', '#portal');
  } else {
    // ── OPERATOR / INTERNAL DEVELOPER CONTROL PLANE ──
    if (clientExecPill) clientExecPill.style.display = 'none';
    if (clientTopbarActions) clientTopbarActions.style.display = 'none';
    if (clientBrandLabel) clientBrandLabel.style.display = 'none';
    if (projectPill) projectPill.style.display = 'inline-flex';
    if (operatorControls) operatorControls.style.display = 'inline-flex';
    if (btnNewProject) btnNewProject.style.display = '';
    if (btnTour) btnTour.style.display = '';
    if (btnNewMission) btnNewMission.style.display = '';
    if (btnGitHub) btnGitHub.style.display = '';
    if (btnSettings) btnSettings.style.display = '';
    if (brandLink) brandLink.setAttribute('href', '#overview');

    if (surfaceSwitcher) {
      surfaceSwitcher.style.display = 'inline-flex';
    }
    if (navAdmin) {
      navAdmin.style.display = isOperator ? '' : 'none';
      navAdmin.classList.toggle('ky-surface-nav-active', activeSurface === 'admin');
    }
    if (navConsole) {
      navConsole.style.display = isOperator ? '' : 'none';
      navConsole.classList.toggle('ky-surface-nav-active', activeSurface === 'console');
    }
    if (navPortal) {
      navPortal.classList.toggle('ky-surface-nav-active', activeSurface === 'portal');
      if (isClientPortal && isOperator) {
        navPortal.setAttribute('title', 'Previewing Executive Client Portal (Operator Mode)');
      }
    }
  }
}

window.syncTriadNavBar = syncTriadNavBar;

async function dispatchTriadSurfaceRoute(routeStr) {
  const view = document.getElementById('view') || document.getElementById('main-content');
  if (!view) return false;
  const raw = (routeStr !== undefined ? routeStr : getEffectiveTriadRoute());
  const [baseRoute, queryPart] = raw.split('?');
  const params = {};
  const searchParams = new URLSearchParams(window.location.search || '');
  if (searchParams.get('org')) params.org = searchParams.get('org');
  if (queryPart) {
    const sp = new URLSearchParams(queryPart);
    if (sp.get('org')) params.org = sp.get('org');
  }
  if (
    baseRoute === 'portal' ||
    baseRoute === 'client-portal' ||
    baseRoute === 'portal-summary' ||
    baseRoute.startsWith('portal/') ||
    baseRoute === 'teamwork-preview' ||
    baseRoute === 'teamwork' ||
    baseRoute === 'boost' ||
    baseRoute === 'team' ||
    baseRoute === 'reports' ||
    baseRoute === 'milestones' ||
    baseRoute === 'deliverables'
  ) {
    if (baseRoute.startsWith('portal/')) {
      params.tab = baseRoute.split('/')[1];
    } else if (baseRoute === 'boost') {
      params.tab = 'overview';
      params.boost = true;
    } else if (baseRoute === 'reports') {
      params.tab = 'reports';
    } else if (baseRoute === 'milestones' || baseRoute === 'deliverables') {
      params.tab = baseRoute;
    }
    syncTriadNavBar('portal');
    const mod = await import('./views/client_portal.js');
    await mod.renderClientPortalView(view, params);
    return true;
  }
  if (baseRoute === 'admin' || baseRoute === 'agency-admin') {
    const tok = localStorage.getItem('kobits_token');
    let role = '';
    try {
      const u = JSON.parse(localStorage.getItem('kobits_user') || '{}');
      role = String(u.role || (u.user && u.user.role) || window.__kobitsActiveOrgRole || '').toUpperCase();
    } catch (_) {}
    const isOp = tok && (role === 'OWNER' || role === 'ADMIN' || role === 'LEAD_ENGINEER' || role === 'AGENCY_ADMIN');
    if (!tok || (role && !isOp)) {
      window.location.href = '/portal.html?auth_notice=admin_required#login';
      return true;
    }
    syncTriadNavBar('admin');
    const mod = await import('./views/admin_console.js');
    await mod.renderAdminConsoleView(view, params);
    return true;
  }
  if (baseRoute === 'console' || baseRoute.startsWith('console/')) {
    const tok = localStorage.getItem('kobits_token');
    let role = '';
    try {
      const u = JSON.parse(localStorage.getItem('kobits_user') || '{}');
      role = String(u.role || (u.user && u.user.role) || window.__kobitsActiveOrgRole || '').toUpperCase();
    } catch (_) {}
    const isOp = tok && (role === 'OWNER' || role === 'ADMIN' || role === 'LEAD_ENGINEER' || role === 'AGENCY_ADMIN');
    if (!tok || (role && !isOp)) {
      window.location.href = '/portal.html?auth_notice=console_required#login';
      return true;
    }
    syncTriadNavBar('console');
    const parts = baseRoute.split('/');
    if (parts[1]) params.missionId = parts[1];
    const mod = await import('./views/console_view.js');
    await mod.renderSystemsConsoleView(view, params);
    return true;
  }
  syncTriadNavBar('');
  return false;
}

// ── Data Fetching ───────────────────────────────────────────────────────────

async function fetchDashboard(silent = false) {
  const view = document.getElementById('view') || document.getElementById('main-content');
  if (!view) return;
  const initialCurH = getEffectiveTriadRoute();
  if (isTriadSurfaceRoute(initialCurH)) {
    if (!silent) await dispatchTriadSurfaceRoute(initialCurH);
    if (!state.dashboard) {
      get('/org/dashboard').then(data => {
        if (data) {
          state.dashboard = data;
          populateProjectSelect(data);
        }
      }).catch(() => {
        populateProjectSelect(null);
      });
    }
    return;
  }
  const isMissionRoute = initialCurH.startsWith('missions') || initialCurH.startsWith('mission');
  if (!silent && !state.dashboard && !isMissionRoute && initialCurH !== 'settings' && initialCurH !== 'new-project') {
    view.innerHTML = `<div class="ky-card" style="text-align:center;padding:48px 24px;max-width:540px;margin:40px auto">
      <div class="ky-eyebrow" style="color:var(--text-3);margin-bottom:8px">LOADING WORKSPACE</div>
      <h2 style="font-size:18px;margin:0 0 10px;font-weight:700">Connecting to Engineering Control Plane…</h2>
      <p style="font-size:13px;color:var(--text-2);margin:0">Synchronizing live engineering telemetry…</p>
    </div>`;
  }
  state._fetching = true;
  try {
    if (state.projectId && state.projectId.startsWith('demo-')) {
      state.projectId = '';
      localStorage.removeItem('kobits.projectId');
    }
    let data;
    const hasSpecificProject = state.projectId && state.projectId !== 'default' && state.projectId !== '__new__';
    const q = hasSpecificProject ? `?project_id=${encodeURIComponent(state.projectId)}` : '';
    try {
      data = await get(`/org/dashboard${q}`);
    } catch (firstErr) {
      if (hasSpecificProject) {
        // Stale projectId in localStorage (e.g. after container reset) — clear and retry default
        state.projectId = '';
        localStorage.removeItem('kobits.projectId');
        data = await get('/org/dashboard');
      } else {
        throw firstErr;
      }
    }
    state.dashboard = data;
    if (data?.project?.id) {
      state.projectId = data.project.id;
      localStorage.setItem('kobits.projectId', state.projectId);
    } else if (data?.projects && data.projects.length > 0) {
      state.projectId = data.projects[0].id;
      localStorage.setItem('kobits.projectId', state.projectId);
    } else {
      state.projectId = '';
      localStorage.removeItem('kobits.projectId');
    }
    populateProjectSelect(data);
    const curH = getEffectiveTriadRoute();
    if (isTriadSurfaceRoute(curH)) {
      return;
    }
    if (curH === 'settings') {
      if (!silent) renderSettingsPage();
      return;
    }
    if (curH === 'new-project') {
      if (!silent) renderNewProjectPage();
      return;
    }
    if (curH.startsWith('missions') || curH.startsWith('mission')) {
      return;
    }
    renderDashboard(silent);
  } catch (err) {
    const errMsg = String(err?.message || err || '');
    const isAuthErr = err?.status === 401 ||
      err?.status === 403 ||
      errMsg.toLowerCase().includes('credential') ||
      errMsg.toLowerCase().includes('not authenticated') ||
      errMsg.toLowerCase().includes('unauthorized') ||
      errMsg.toLowerCase().includes('forbidden');

    if (isAuthErr) {
      localStorage.removeItem('kobits_token');
      localStorage.removeItem('kobits_user');
      if (!silent) {
        view.innerHTML = `
          <div class="ky-card" style="max-width:540px;margin:50px auto;padding:36px 28px;text-align:center;border-color:var(--border-strong)">
            <div class="ky-eyebrow" style="color:var(--text-3);margin-bottom:8px">WORKSPACE AUTHENTICATION</div>
            <h2 style="font-size:18px;font-weight:700;margin:0 0 8px;letter-spacing:-0.01em">Session Expired or Not Logged In</h2>
            <p style="font-size:13px;color:var(--text-2);line-height:1.6;margin:0 0 24px">
              Your session has ended or credentials could not be validated. Please log in or register to connect to your Kobits workspace.
            </p>
            <div style="display:flex;gap:10px;justify-content:center;align-items:center;flex-wrap:wrap">
              <a id="ky-btn-expired-signin" href="/portal.html?logout=1#login" class="ky-btn ky-btn-primary ky-btn-sm" style="display:inline-flex;align-items:center;gap:6px;text-decoration:none">
                <span>Sign In to Kobits</span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>
              </a>
              <a href="/portal.html?logout=1#register" class="ky-btn ky-btn-ghost ky-btn-sm" style="border:1px solid var(--border);text-decoration:none">
                Register Free
              </a>
              <a href="?demo=true" class="ky-btn ky-btn-ghost ky-btn-sm" style="border:1px solid var(--border);text-decoration:none">
                Demo Workspace
              </a>
              <a href="/landing.html" class="ky-btn ky-btn-ghost ky-btn-sm" style="border:1px solid var(--border);text-decoration:none">
                Home
              </a>
            </div>
          </div>
        `;

        document.getElementById('ky-btn-expired-signin')?.addEventListener('click', async (e) => {
          e.preventDefault();
          try {
            await fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'include' });
          } catch (_) {}
          localStorage.removeItem('kobits_token');
          localStorage.removeItem('kobits_user');
          window.location.href = '/portal.html?logout=1#login';
        });
      }
      return;
    }

    if (!silent) {
      view.innerHTML = `
        <div class="ky-card" style="border-color:var(--err-border);max-width:600px;margin:32px auto;padding:24px">
          <div class="ky-eyebrow" style="color:var(--err-fg)">TELEMETRY NOTICE</div>
          <p style="margin:8px 0 16px;font-size:13px;color:var(--text-2)">${esc(errMsg)}</p>
          <div style="display:flex;gap:10px;flex-wrap:wrap">
            <button type="button" class="ky-btn ky-btn-primary ky-btn-xs" onclick="window.fetchDashboard ? window.fetchDashboard() : window.location.reload()">Retry Connection</button>
            <a href="/portal.html?logout=1#login" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border);text-decoration:none">Sign In Again</a>
            <a href="?demo=true" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border);text-decoration:none">Explore Demo Workspace</a>
          </div>
        </div>
      `;
    }
  } finally {
    state._fetching = false;
  }
}
window.fetchDashboard = fetchDashboard;

function populateProjectSelect(data) {
  const sel = document.getElementById('ky-project-select');
  if (sel) {
    const projects = data?.projects || [];
    const activeId = data?.project?.id || state.projectId || (projects[0]?.id) || '';
    if (projects.length > 0) {
      sel.innerHTML = projects.map((p) =>
        `<option value="${esc(p.id)}" ${p.id === activeId ? 'selected' : ''}>${esc(p.name)}</option>`
      ).join('') + `<option value="__new__">+ Create New Project…</option>`;
      if (!state.projectId && activeId) {
        state.projectId = activeId;
        localStorage.setItem('kobits.projectId', activeId);
      }
    } else {
      sel.innerHTML = `
        <option value="default" selected>Kobits Core Workspace</option>
        <option value="__new__">+ Create New Project…</option>
      `;
    }
  }
  updateTopBarGitHubStatus(data?.github || _settingsCache.github);
}

function updateTopBarGitHubStatus(gh) {
  const ghLabel = document.getElementById('ky-topbar-gh-label');
  const ghBtn = document.getElementById('ky-topbar-github');
  if (!ghLabel || !ghBtn) return;
  if (gh && gh.connected && gh.username) {
    ghLabel.textContent = `@${gh.username}`;
    ghBtn.style.color = 'var(--text-1)';
    ghBtn.style.borderColor = 'var(--text-1)';
    ghBtn.title = `Connected to GitHub as @${gh.username} (Click to manage)`;
  } else {
    ghLabel.textContent = 'Connect GitHub';
    ghBtn.style.color = 'var(--text-2)';
    ghBtn.style.borderColor = 'var(--border)';
    ghBtn.title = 'Click to connect your GitHub Personal Access Token in Settings';
  }
}

// ── Main Kyros View Renderer ────────────────────────────────────────────────

function renderDashboard(silent = false) {
  const view = document.getElementById('view') || document.getElementById('main-content');
  if (!view) return;
  const d = state.dashboard;
  if (!d) {
    view.innerHTML = `
      <div class="ky-card" style="text-align:center;padding:48px 24px;max-width:540px;margin:40px auto">
        <div class="ky-eyebrow" style="color:var(--text-3);margin-bottom:8px">INITIALIZING WORKSPACE</div>
        <h2 style="font-size:18px;margin:0 0 10px;font-weight:700">Connecting to Engineering Control Plane…</h2>
        <p style="font-size:13px;color:var(--text-2);margin:0 0 24px">Synchronizing telemetry, active branches, and autonomous agent runtime.</p>
        <div style="display:flex;gap:10px;justify-content:center;align-items:center;flex-wrap:wrap">
          <button type="button" class="ky-btn ky-btn-primary ky-btn-xs" onclick="window.fetchDashboard ? window.fetchDashboard() : window.location.reload()">
            Retry Connection
          </button>
          <a href="/portal.html#login" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border);text-decoration:none">
            Sign In
          </a>
          <a href="?demo=true" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border);text-decoration:none">
            Demo Workspace
          </a>
        </div>
      </div>
    `;
    if (!state._fetching) {
      fetchDashboard();
    }
    return;
  }

  const hasRealProject = Boolean(d.project && d.project.id);
  const proj = d.project || {
    id: 'default',
    name: 'Default Engineering Workspace',
    description: 'Launch a mission below to auto-initialize your workspace, or create a named project.',
  };
  const pct = d.progress?.percent ?? 0;
  // Tag each mission with its chronological Sprint number (Sprint 1 = oldest, Sprint N = newest)
  const rawMilestones = d.milestones || [];
  const totalMissions = rawMilestones.length;
  const milestones = rawMilestones.map((m, idx) => ({
    ...m,
    sprintNumber: totalMissions - idx,
  }));

  // Capture user drafts, focus, and selection range before DOM updates
  const existingPrompt = document.getElementById('ky-mission-prompt');
  if (existingPrompt && existingPrompt.value) {
    state.draftPrompt = existingPrompt.value;
  }
  const isPromptFocused = existingPrompt ? (document.activeElement === existingPrompt) : false;
  const promptSelStart = existingPrompt ? existingPrompt.selectionStart : null;
  const promptSelEnd = existingPrompt ? existingPrompt.selectionEnd : null;

  const existingBranch = document.getElementById('ky-mission-branch');
  if (existingBranch) state.draftBranch = existingBranch.value;

  const existingMode = document.getElementById('ky-mission-mode');
  if (existingMode) state.draftMode = existingMode.value;

  const existingSteer = document.getElementById('ky-steer-input');
  const isSteerFocused = existingSteer ? (document.activeElement === existingSteer) : false;
  const savedSteer = existingSteer ? existingSteer.value : '';

  // SMART NON-DESTRUCTIVE REFRESH for background 8s polls:
  // If already rendered on Overview, update ONLY telemetry without touching the prompt input!
  const sprintContainer = document.getElementById('ky-current-sprint-container');
  if (silent && sprintContainer && state.tab === 'overview') {
    const totalTasks = Number(d.progress?.total_tasks || 0);
    const completedTasks = Number(d.progress?.completed_tasks || 0);
    const remainingTasks = Math.max(0, totalTasks - completedTasks);
    const spend = d.spend || {};
    const deliveredCount = milestones.filter(
      (m) => m.status === 'COMPLETED' || (m.files_changed && m.files_changed > 0)
    ).length;
    const totalMilestones = milestones.length || (d.missions?.total ?? 0);

    const selectedMission = milestones.find((m) => m.mission_id === state.selectedMissionId)
      || (d.current ? milestones.find((m) => m.mission_id === d.current.mission_id) || d.current : null)
      || milestones[0]
      || null;

    const sprintTasks = selectedMission?.tasks || [];
    const sprintDone = selectedMission?.tasks_done ?? sprintTasks.filter((t) => t.status === 'COMPLETED').length;
    const sprintTotal = selectedMission?.tasks_total ?? sprintTasks.length;
    const sprintNum = selectedMission?.sprintNumber || milestones.length || 1;
    const isRunning = selectedMission && RUNNING.has(selectedMission.status);
    const recentDeliverables = milestones.slice(0, 6);

    // 1. Update header progress
    const pctEl = document.querySelector('.ky-proj-pct');
    if (pctEl) pctEl.textContent = d.progress?.percent != null ? `${pct}%` : DASH;
    const barEl = document.querySelector('.ky-master-bar span');
    if (barEl) barEl.style.width = `${pct}%`;

    // 2. Update KPI Grid
    const kpiGrid = document.querySelector('.ky-kpi-grid');
    if (kpiGrid) kpiGrid.innerHTML = renderKpiGridHtml(pct, deliveredCount, totalMilestones, spend, remainingTasks, d);

    // 3. Update Current Sprint Card
    sprintContainer.innerHTML = renderCurrentSprintHtml(selectedMission, sprintNum, milestones, sprintTasks, sprintDone, sprintTotal, isRunning);

    // 4. Update Recent Deliverables
    const delivsContainer = document.getElementById('ky-recent-deliverables-container');
    if (delivsContainer) delivsContainer.innerHTML = renderRecentDeliverablesHtml(recentDeliverables, milestones);

    // Re-wire sprint actions without re-binding or touching the prompt textarea
    wireSprintEvents();
    return;
  }

  view.innerHTML = `
    <!-- Kyros Project Header & Master Progress Bar -->
    <section class="ky-proj-header">
      <div class="ky-proj-top">
        <div>
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
            <h1 class="ky-proj-title">${esc(proj.name)}</h1>
            <button type="button" data-open-new-project="1" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border);font-family:var(--font-mono);font-size:11px">
              + ${hasRealProject ? 'New Project' : 'Create Named Project'}
            </button>
          </div>
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
      <button type="button" class="ky-tab ${location.hash === '#settings' ? 'active' : ''}" id="ky-tab-settings-nav" title="Workspace & Account Settings">
        <span>Settings</span>
      </button>
    </nav>

    <!-- Tab Body -->
    <div id="ky-tab-body">
      ${renderActiveTab(d, milestones)}
    </div>
  `;

  // Restore prompt text, selection, and focus state if user had input
  const newPrompt = document.getElementById('ky-mission-prompt');
  if (newPrompt) {
    if (state.draftPrompt) newPrompt.value = state.draftPrompt;
    if (isPromptFocused) {
      newPrompt.focus();
      if (promptSelStart !== null && promptSelEnd !== null) {
        try { newPrompt.setSelectionRange(promptSelStart, promptSelEnd); } catch {}
      }
    }
  }

  const newBranch = document.getElementById('ky-mission-branch');
  if (newBranch && state.draftBranch) newBranch.value = state.draftBranch;

  const newMode = document.getElementById('ky-mission-mode');
  if (newMode && state.draftMode) newMode.value = state.draftMode;

  const newSteer = document.getElementById('ky-steer-input');
  if (newSteer && savedSteer) {
    newSteer.value = savedSteer;
    if (isSteerFocused) newSteer.focus();
  }

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

  const isRunning = selectedMission && RUNNING.has(selectedMission.status);
  const gh = d.github || _settingsCache.github || {};
  const ghConnected = Boolean(gh.connected && gh.username);

  return `
    <!-- GitHub Integration Status Card -->
    ${!ghConnected ? `
      <div class="ky-card" style="padding:14px 18px;margin-bottom:18px;background:var(--surface-2);border-color:var(--border-strong);display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:12px">
          <div style="width:34px;height:34px;border-radius:6px;background:var(--bg);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;flex-shrink:0">
            <svg width="18" height="18" fill="currentColor" viewBox="0 0 24 24"><path fill-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" clip-rule="evenodd"/></svg>
          </div>
          <div>
            <div style="font-weight:600;font-size:13.5px;display:flex;align-items:center;gap:8px">
              <span>Connect Your GitHub Account</span>
              <span class="ky-badge" style="font-size:10px">Setup Required</span>
            </div>
            <div style="font-size:12px;color:var(--text-2);margin-top:2px">Connect a Personal Access Token (PAT) so autonomous agents can clone repositories, run sandboxes, and create Pull Requests.</div>
          </div>
        </div>
        <button type="button" class="ky-btn ky-btn-primary ky-btn-xs" onclick="window.openGitHubModal?.()" style="white-space:nowrap;display:inline-flex;align-items:center;gap:6px">
          <svg width="13" height="13" fill="currentColor" viewBox="0 0 24 24"><path fill-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" clip-rule="evenodd"/></svg>
          <span>Connect GitHub Account →</span>
        </button>
      </div>
    ` : `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;padding:7px 12px;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;font-size:11.5px">
        <div style="display:flex;align-items:center;gap:8px">
          <svg width="14" height="14" fill="currentColor" viewBox="0 0 24 24"><path fill-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" clip-rule="evenodd"/></svg>
          <span style="color:var(--text-2)">GitHub Connected:</span>
          <span style="font-weight:600;color:var(--text-1)">@${esc(gh.username)}</span>
        </div>
        <button type="button" onclick="window.openGitHubModal?.()" class="ky-btn ky-btn-ghost ky-btn-xs" style="color:var(--text-2);border:1px solid var(--border)">
          Manage Token
        </button>
      </div>
    `}

    <!-- Kyros Mission Launch Console (Interactive Input) -->
    <div class="ky-prompt-console">
      <div class="ky-prompt-header">
        <div class="ky-prompt-telemetry">
          <span class="dot"></span>
          <span>KOBITS REASONING LOOP // DUAL-LOOP ORCHESTRATION</span>
        </div>
        <div class="ky-prompt-selectors">
          <select id="ky-mission-mode" class="ky-prompt-select" title="Execution Mode">
            <option value="autopilot">⚡ Autopilot (Autonomous)</option>
            <option value="approval_required">🛡 Requires Approval</option>
          </select>
          <input id="ky-mission-branch" class="ky-prompt-input-sm" value="main" placeholder="Branch" style="width:75px" title="Target Branch">
        </div>
      </div>
      <textarea id="ky-mission-prompt" class="ky-prompt-textarea" placeholder="What should Kobits build, refactor, or test? (e.g. 'Add Stripe webhook verification with HMAC-SHA256 and pytest assertions')"></textarea>
      <div class="ky-prompt-suggestions" style="display:flex;align-items:center;gap:6px;padding:6px 14px 4px;flex-wrap:wrap">
        <span style="font-size:10.5px;color:var(--text-3);font-family:var(--font-mono)">SUGGESTIONS:</span>
        <button type="button" class="ky-btn ky-btn-ghost ky-btn-xs" style="font-size:11px;padding:2px 8px;border-radius:4px;border:1px solid var(--border)" onclick="window.kySetPrompt?.('Audit repository for OWASP Top 10 security vulnerabilities and exposed secrets')">🔍 Security Audit</button>
        <button type="button" class="ky-btn ky-btn-ghost ky-btn-xs" style="font-size:11px;padding:2px 8px;border-radius:4px;border:1px solid var(--border)" onclick="window.kySetPrompt?.('Synthesize comprehensive pytest test suite with regression edge cases')">🧪 Pytest Suite</button>
        <button type="button" class="ky-btn ky-btn-ghost ky-btn-xs" style="font-size:11px;padding:2px 8px;border-radius:4px;border:1px solid var(--border)" onclick="window.kySetPrompt?.('Implement production health check endpoint with database ping and telemetry')">⚡ Health Check</button>
      </div>
      <div class="ky-prompt-footer">
        <div class="ky-prompt-hints">
          <span><span>↵</span> Enter to submit</span>
          <span>·</span>
          <span>MicroVM cleanroom verified</span>
        </div>
        <button id="ky-mission-submit" type="button" class="ky-prompt-btn-launch">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          Launch Mission
        </button>
      </div>
    </div>

    <!-- 4 Kyros KPI Stat Cards (COMPLETE | DELIVERABLES | SPENT | REMAINING) -->
    <div class="ky-kpi-grid">
      ${renderKpiGridHtml(pct, deliveredCount, totalMilestones, spend, remainingTasks, d)}
    </div>

    <!-- Full-Width CURRENT SPRINT Card (Exact Kyros Overview Layout) -->
    <div id="ky-current-sprint-container">
      ${renderCurrentSprintHtml(selectedMission, sprintNum, milestones, sprintTasks, sprintDone, sprintTotal, isRunning)}
    </div>

    <!-- RECENT DELIVERABLES Section (Exact Kyros Overview Bottom Section) -->
    <div id="ky-recent-deliverables-container" style="margin-top:26px">
      ${renderRecentDeliverablesHtml(recentDeliverables, milestones)}
    </div>
  `;
}

function renderKpiGridHtml(pct, deliveredCount, totalMilestones, spend, remainingTasks, d) {
  return `
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
      <div class="ky-kpi-val">${fmtUsd(spend?.usd)}</div>
    </div>
    <div class="ky-kpi-card">
      <div class="ky-kpi-label">REMAINING</div>
      <div class="ky-kpi-val">${remainingTasks > 0 ? `${num(remainingTasks)} tasks` : duration(d?.avg_duration_seconds)}</div>
    </div>
  `;
}

function renderCurrentSprintHtml(selectedMission, sprintNum, milestones, sprintTasks, sprintDone, sprintTotal, isRunning) {
  return `
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
                ${t.agent ? `<span class="ky-task-agent">${agentGlyph(t.agent, 12)}${esc(titleCase(t.agent))}</span>` : ''}
                ${taskStatusBadge(t.status)}
              </div>
            </div>
          `).join('') : `<div style="color:var(--text-3);padding:16px 0">No tasks recorded for this sprint yet.</div>`}

          ${isRunning ? `
            <!-- Mid-Flight Real-Time Steering Bar -->
            <div class="ky-steer-bar">
              <div class="ky-steer-label">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                <span>STEER IN REAL TIME:</span>
              </div>
              <input id="ky-steer-input" class="ky-steer-input" type="text" placeholder="Type instructions to guide the running agents mid-flight...">
              <button id="ky-steer-btn" type="button" class="ky-btn ky-btn-primary ky-btn-xs" data-steer-id="${esc(selectedMission.mission_id)}">Steer</button>
            </div>
          ` : ''}
        </div>
      ` : `<div style="color:var(--text-3);padding:20px 0">Use the prompt box above or CLI to launch a mission and track sprint tasks.</div>`}
    </div>
  `;
}

function renderRecentDeliverablesHtml(recentDeliverables, milestones) {
  return `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
      <div class="ky-eyebrow">RECENT DELIVERABLES</div>
      <button type="button" class="ky-btn ky-btn-ghost ky-btn-xs" data-goto-tab="deliverables">View All (${milestones.length}) →</button>
    </div>
    ${recentDeliverables.length ? `
      <div class="ky-deliv-grid">
        ${recentDeliverables.map(renderDeliverableCard).join('')}
      </div>
    ` : `<div class="ky-card" style="color:var(--text-3)">No deliverables recorded yet.</div>`}
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
                  <div style="font-weight:700;font-size:14.5px">${agentGlyph(a.type, 16)}${esc(a.name)}</div>
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
              <div style="font-weight:600">${agentGlyph(a.type, 14)}${esc(a.name)} <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-3)">(${esc(titleCase(a.type))})</span></div>
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

function wireSprintEvents() {
  const sprintSel = document.getElementById('ky-sprint-select');
  if (sprintSel) {
    sprintSel.addEventListener('change', () => {
      state.selectedMissionId = sprintSel.value;
      renderDashboard();
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

  // Mid-Flight Steering
  const steerInput = document.getElementById('ky-steer-input');
  const steerBtn = document.getElementById('ky-steer-btn');
  if (steerBtn && steerInput) {
    const doSteer = async () => {
      const text = steerInput.value.trim();
      const mid = steerBtn.dataset.steerId;
      if (!text || !mid) return;
      steerBtn.disabled = true;
      try {
        await post(`/missions/${encodeURIComponent(mid)}/steer`, { instruction: text });
        notify('💬 Steering instruction injected into agent context!');
        steerInput.value = '';
        await fetchDashboard(true);
      } catch (err) {
        notify(`Steer failed: ${err.message}`, 'error');
      } finally {
        steerBtn.disabled = false;
      }
    };
    steerBtn.addEventListener('click', doSteer);
    steerInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        doSteer();
      }
    });
  }
}

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

  const settingsTab = document.getElementById('ky-tab-settings-nav');
  if (settingsTab) {
    settingsTab.addEventListener('click', () => {
      location.hash = '#settings';
      renderSettingsPage();
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

  // Wire dynamic sprint cards, inspector, and steering
  wireSprintEvents();

  // New Project triggers inside the dashboard header
  document.querySelectorAll('[data-open-new-project]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openNewProjectModal();
    });
  });

  // Mission Launch Console
  const promptInput = document.getElementById('ky-mission-prompt');
  const launchBtn = document.getElementById('ky-mission-submit');
  if (launchBtn && promptInput) {
    promptInput.addEventListener('input', (e) => {
      state.draftPrompt = e.target.value;
    });

    const doLaunch = async () => {
      const text = promptInput.value.trim();
      if (!text) {
        notify('Please enter a task objective for Kobits.', 'error');
        promptInput.focus();
        return;
      }
      const branch = (document.getElementById('ky-mission-branch')?.value || 'main').trim();
      const mode = document.getElementById('ky-mission-mode')?.value || 'autopilot';
      launchBtn.disabled = true;
      launchBtn.innerHTML = `<span>⚡ Booting sandbox…</span>`;

      try {
        const title = text.length > 60 ? text.slice(0, 60) + '…' : text;
        const targetPid = (state.projectId && state.projectId !== '__new__' && !state.projectId.startsWith('demo-'))
          ? state.projectId
          : (state.dashboard?.project?.id || 'default');
        const res = await post(`/missions/projects/${encodeURIComponent(targetPid)}/missions`, {
          title: title,
          objective: text,
          branch: branch,
          execution_mode: mode,
          auto_execute: true
        });

        if (res?.project_id) {
          state.projectId = res.project_id;
          localStorage.setItem('kobits.projectId', res.project_id);
        }
        notify('🚀 Mission launched into cleanroom sandbox!');
        promptInput.value = '';
        state.selectedMissionId = res.mission_id || res.id;
        const mId = res.mission_id || res.id;
        if (mId) {
          if (location.hash === `#missions/${mId}`) {
            import('./views/mission.js').then(mod => mod.render(mId));
          } else {
            location.hash = `#missions/${mId}`;
          }
        } else {
          await fetchDashboard(true);
        }
      } catch (err) {
        notify(`Failed to launch mission: ${err.message}`, 'error');
        launchBtn.disabled = false;
        launchBtn.innerHTML = `
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          Launch Mission
        `;
      }
    };

    launchBtn.addEventListener('click', doLaunch);
    promptInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey || !e.shiftKey)) {
        e.preventDefault();
        doLaunch();
      }
    });
  }
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
              <h3 style="margin:2px 0 0;font-size:18px">${agentGlyph(agent.type, 18)}${esc(agent.name)}</h3>
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
            <a href="#/missions/${esc(missionId)}" class="ky-btn ky-btn-xs" style="text-decoration:none;display:inline-flex;align-items:center;gap:4px" title="Open Mission Live Terminal" onclick="document.getElementById('ky-modal-host').innerHTML=''">
              <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#22c55e"></span>
              <span>Live Terminal &rarr;</span>
            </a>
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
              ${files.map((f, idx) => {
                const filePath = typeof f === 'string' ? f : (f.path || 'file');
                const fileAdded = typeof f === 'object' ? (f.added ?? 142) : 142;
                const fileRemoved = typeof f === 'object' ? (f.removed ?? 0) : 0;
                return `
                <button type="button" class="ky-btn ky-btn-xs ${idx === selectedFileIdx ? 'ky-btn-primary' : ''}" data-file-idx="${idx}">
                  ${esc(filePath)} (+${fileAdded}/-${fileRemoved})
                </button>
              `}).join('')}
            </div>
          </div>
          ${activeFile
            ? renderDiff(typeof activeFile === 'object' && activeFile.diff ? activeFile.diff : (diffData.diff_text || '')).replace('class="diff"', 'class="ky-diff-wrap"').replace('<table>', '<table class="ky-diff-table">')
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
                  ${t.agent_role ? `<span class="ky-task-agent">${agentGlyph(t.agent_role, 12)}${esc(titleCase(t.agent_role))}</span>` : ''}
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
                  <div style="font-weight:600;font-size:12.5px">${agentGlyph(a.agent_role || a.agent_name || '', 13)}${esc(a.agent_name || titleCase(a.agent_role || 'Specialist'))} · ${esc(a.task_title || '')}</div>
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

    const runApply = async (dryRun, force = false) => {
      try {
        const res = await post(`/missions/${encodeURIComponent(missionId)}/apply`, { dry_run: dryRun, force: force });
        const appliedCount = res.applied_count ?? (res.applied || []).length;
        const skippedCount = res.skipped_count ?? (res.skipped || []).length;
        const conflictCount = res.conflict_count ?? (res.conflicts || []).length;
        const hasConflicts = conflictCount > 0;
        const banner = `
          <div class="ky-card" style="margin:10px 0;border-color:${hasConflicts ? 'var(--err-fg, #ef4444)' : 'var(--accent-border)'};background:var(--bg-elevated)">
            <div class="ky-eyebrow" style="color:${hasConflicts ? 'var(--err-fg, #ef4444)' : 'inherit'}">${dryRun ? 'DRY-RUN MERGE PREVIEW' : 'SANDBOX APPLIED TO WORKSPACE'} · ${hasConflicts ? 'CONFLICT DETECTED' : 'DONE'}</div>
            <div style="font-family:var(--font-mono);font-size:12px;margin-top:6px">
              Applied: ${appliedCount} · Unchanged/Skipped: ${skippedCount} · Conflicts: ${conflictCount}
              ${res.workspace_dir ? `<div style="color:var(--text-3);margin-top:4px">Target: ${esc(res.workspace_dir)}</div>` : ''}
              ${hasConflicts ? `
                <div style="margin-top:8px;padding:8px 10px;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.25);border-radius:4px;color:var(--err-fg, #ef4444)">
                  <strong>Conflicts detected in ${conflictCount} file(s):</strong>
                  <ul style="margin:4px 0 8px 16px;padding:0;font-size:11.5px">
                    ${(res.conflicts || []).map(c => `<li><code>${esc(c.path)}</code> — ${esc(c.reason || 'content conflict')}</li>`).join('')}
                  </ul>
                  <button type="button" class="ky-btn ky-btn-primary ky-btn-xs" id="ky-act-force-apply" style="background:#ef4444;border-color:#dc2626">Force Apply (Overwrite Conflicting Files)</button>
                </div>
              ` : ''}
            </div>
          </div>
        `;
        notify(dryRun ? `Dry-run: ${appliedCount} file(s) ready${hasConflicts ? ` (${conflictCount} conflict)` : ''}` : (hasConflicts ? `Apply blocked by ${conflictCount} conflict(s)` : `Applied ${appliedCount} file(s) to workspace`));
        renderBody(banner);
        document.getElementById('ky-act-force-apply')?.addEventListener('click', () => runApply(false, true));
        if (!dryRun && !hasConflicts) await fetchDashboard(true);
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
    if (projSel.value === '__new__') {
      projSel.value = state.projectId || 'default';
      openNewProjectModal();
      return;
    }
    state.projectId = projSel.value === 'default' ? '' : projSel.value;
    state.selectedMissionId = null;
    if (state.projectId) {
      localStorage.setItem('kobits.projectId', state.projectId);
    } else {
      localStorage.removeItem('kobits.projectId');
    }
    fetchDashboard();
  });

  const newProjBtn = document.getElementById('ky-topbar-new-project');
  newProjBtn?.addEventListener('click', () => {
    openNewProjectModal();
  });

  const tourBtn = document.getElementById('ky-topbar-tour');
  tourBtn?.addEventListener('click', () => {
    openOnboardingWalkthrough(1);
  });

  const newBtn = document.getElementById('ky-topbar-new');
  newBtn?.addEventListener('click', () => {
    openCreateMissionModal();
  });

  const settingsBtn = document.getElementById('ky-topbar-settings');
  settingsBtn?.addEventListener('click', () => {
    openSettingsModal('account');
  });

  const ghBtn = document.getElementById('ky-topbar-github');
  ghBtn?.addEventListener('click', (e) => {
    e.preventDefault();
    openGitHubModal();
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const host = document.getElementById('ky-modal-host');
      if (host) host.innerHTML = '';
      return;
    }
    if ((e.key === 'n' || e.key === 'N') && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const tag = document.activeElement?.tagName?.toLowerCase();
      if (tag !== 'input' && tag !== 'textarea' && tag !== 'select' && !document.activeElement?.isContentEditable) {
        e.preventDefault();
        openCreateMissionModal();
      }
    }
  });

  window.addEventListener('hashchange', () => {
    const rawH = (location.hash || '').replace(/^#\/?/, '');
    const h = rawH.split('?')[0];
    if (isTriadSurfaceRoute(rawH)) {
      dispatchTriadSurfaceRoute(rawH);
      return;
    }
    if (h === 'github') {
      openGitHubModal();
      return;
    }
    if (h === 'settings') {
      renderSettingsPage();
      return;
    }
    if (h === 'new-project') {
      renderNewProjectPage();
      return;
    }
    if (h.startsWith('missions/') || h.startsWith('mission/')) {
      const parts = h.split('/');
      const mId = parts[1];
      if (mId) {
        import('./views/mission.js').then(mod => mod.render(mId));
        return;
      }
    }
    if (h === 'missions') {
      import('./views/missions.js').then(mod => mod.render());
      return;
    }
    if (['overview', 'milestones', 'deliverables', 'team', 'reports'].includes(h)) {
      state.tab = h;
      renderDashboard();
    }
  });
}

// ── Standard New Project Page ───────────────────────────────────────────────

function renderNewProjectPage() {
  const view = document.getElementById('view') || document.getElementById('main-content');
  if (!view) return;

  const host = document.getElementById('ky-modal-host');
  if (host) host.innerHTML = '';

  const gh = _settingsCache.github || (typeof localStorage !== 'undefined' && localStorage.getItem('kobits.gh_user') ? { connected: true, username: localStorage.getItem('kobits.gh_user') } : {});
  const ghConnected = Boolean(gh && gh.connected && gh.username);

  view.innerHTML = `
    <div class="ky-newproject-container" style="max-width:680px;margin:0 auto;padding:12px 0 60px">
      <!-- Back Navigation & Header -->
      <div style="margin-bottom:24px">
        <a href="#overview" class="ky-btn ky-btn-ghost ky-btn-xs" style="margin-bottom:12px;display:inline-flex;align-items:center;gap:6px">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
          <span>Back to Dashboard</span>
        </a>
        <h1 style="font-size:24px;font-weight:700;margin:0;letter-spacing:-0.02em">Create a new project</h1>
        <p style="color:var(--text-2);margin:6px 0 0;font-size:13.5px">A project organizes your codebase, automated engineering missions, and team deliverables.</p>
      </div>

      <!-- Main Form Card -->
      <div class="ky-card" style="padding:28px">
        <form id="ky-np-form" onsubmit="return false;">
          <!-- Project Name -->
          <div style="margin-bottom:20px">
            <label for="ky-np-name" style="display:block;font-size:13px;font-weight:600;margin-bottom:6px">
              Project Name <span style="color:var(--err-fg, #ef4444)">*</span>
            </label>
            <input id="ky-np-name" type="text" placeholder="e.g. my-awesome-app" required autofocus style="width:100%;padding:10px 13px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13.5px;color:var(--text-1);outline:none" />
            <div style="font-size:12px;color:var(--text-3);margin-top:5px">Great project names are short and memorable.</div>
          </div>

          <!-- Description -->
          <div style="margin-bottom:20px">
            <label for="ky-np-desc" style="display:block;font-size:13px;font-weight:600;margin-bottom:6px">
              Description <span style="font-size:11.5px;font-weight:400;color:var(--text-3)">(optional)</span>
            </label>
            <input id="ky-np-desc" type="text" placeholder="e.g. Next.js customer portal with Stripe integration" style="width:100%;padding:10px 13px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13.5px;color:var(--text-1);outline:none" />
            <div style="font-size:12px;color:var(--text-3);margin-top:5px">Brief summary of what this project does or the stack it uses.</div>
          </div>

          <!-- GitHub Repo URL -->
          <div style="margin-bottom:20px">
            <label for="ky-np-repo" style="display:block;font-size:13px;font-weight:600;margin-bottom:6px">
              Git Repository URL <span style="font-size:11.5px;font-weight:400;color:var(--text-3)">(optional)</span>
            </label>
            <input id="ky-np-repo" type="text" placeholder="https://github.com/organization/repository" style="width:100%;padding:10px 13px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13px;font-family:var(--font-mono);color:var(--text-1);outline:none" />
            <div style="font-size:12px;color:var(--text-3);margin-top:5px">Connect an existing GitHub repo, or leave blank to start a new workspace.</div>

            <!-- GitHub Account Status / PAT Helper -->
            <div style="margin-top:10px;padding:10px 13px;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;font-size:12px;display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap">
              <div style="display:flex;align-items:center;gap:8px">
                <svg width="14" height="14" fill="currentColor" viewBox="0 0 24 24"><path fill-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" clip-rule="evenodd"/></svg>
                ${ghConnected ? `
                  <span style="color:var(--text-2)">GitHub: <strong style="color:var(--text-1)">@${esc(gh.username)}</strong> connected for automated branch creation</span>
                ` : `
                  <span style="color:var(--text-2)">GitHub account not connected yet.</span>
                `}
              <button type="button" onclick="window.openGitHubModal?.()" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border)">
                ${ghConnected ? 'Manage Token' : 'Connect PAT →'}
              </button>
            </div>
          </div>

          <!-- Default Branch -->
          <div style="margin-bottom:26px">
            <label for="ky-np-branch" style="display:block;font-size:13px;font-weight:600;margin-bottom:6px">
              Default Branch
            </label>
            <input id="ky-np-branch" type="text" value="main" style="width:160px;padding:9px 12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13px;font-family:var(--font-mono);color:var(--text-1);outline:none" />
          </div>

          <!-- Actions -->
          <div style="border-top:1px solid var(--border);padding-top:20px;display:flex;align-items:center;justify-content:space-between">
            <a href="#overview" class="ky-btn ky-btn-ghost ky-btn-sm">Cancel</a>
            <button id="ky-np-submit" type="button" class="ky-btn ky-btn-primary ky-btn-sm" style="padding:9px 20px;font-size:13px">
              Create Project
            </button>
          </div>
        </form>
      </div>
    </div>
  `;

  const nameInput = document.getElementById('ky-np-name');
  const descInput = document.getElementById('ky-np-desc');
  const repoInput = document.getElementById('ky-np-repo');
  const submitBtn = document.getElementById('ky-np-submit');

  setTimeout(() => nameInput?.focus(), 50);

  const handleCreate = async () => {
    const name = nameInput?.value?.trim();
    const description = descInput?.value?.trim() || '';
    const repository_url = repoInput?.value?.trim() || null;

    if (!name) {
      notify('Please enter a project name.', 'error');
      nameInput?.focus();
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Creating…';

    try {
      const created = await post('/projects', { name, description, repository_url });
      const newPid = created?.id || 'default';
      state.projectId = newPid;
      localStorage.setItem('kobits.projectId', newPid);
      state.selectedMissionId = null;
      notify(`Project "${name}" created!`);

      location.hash = '#overview';
      await fetchDashboard();
    } catch (err) {
      notify(err.message || 'Failed to create project.', 'error');
      submitBtn.disabled = false;
      submitBtn.textContent = 'Create Project';
    }
  };

  submitBtn?.addEventListener('click', handleCreate);
  nameInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleCreate();
    }
  });
}

function openNewProjectModal() {
  location.hash = '#new-project';
  renderNewProjectPage();
}

function openCreateMissionModal() {
  const host = document.getElementById('ky-modal-host');
  if (!host) return;

  if (state.projectId && state.projectId.startsWith('demo-')) {
    state.projectId = '';
    localStorage.removeItem('kobits.projectId');
  }
  const d = state.dashboard;
  const projects = d?.projects || [];
  const curPid = (state.projectId && !state.projectId.startsWith('demo-')) ? state.projectId : ((projects[0]?.id) || 'default');

  let projectOptionsHtml = '';
  if (projects.length > 0) {
    projectOptionsHtml = projects.map(p => 
      `<option value="${esc(p.id)}" ${p.id === curPid ? 'selected' : ''}>${esc(p.name)}</option>`
    ).join('') + `<option value="__new__">+ Create New Project…</option>`;
  } else {
    projectOptionsHtml = `<option value="default" selected>Primary Engineering Workspace</option><option value="__new__">+ Create New Project…</option>`;
    if (!state._fetching) {
      get('/org/dashboard').then(dash => {
        if (dash?.projects?.length) {
          state.dashboard = dash;
          const sel = document.getElementById('ky-cm-project');
          if (sel) {
            sel.innerHTML = dash.projects.map(p =>
              `<option value="${esc(p.id)}" ${p.id === (state.projectId || dash.projects[0].id) ? 'selected' : ''}>${esc(p.name)}</option>`
            ).join('') + `<option value="__new__">+ Create New Project…</option>`;
          }
        }
      }).catch(() => {});
    }
  }

  host.innerHTML = `
    <div class="ky-modal-overlay" id="ky-cm-overlay">
      <div class="ky-modal" style="max-width:580px" role="dialog" aria-modal="true">
        <div class="ky-modal-head" style="align-items:flex-start">
          <div style="display:flex;align-items:center;gap:12px">
            <div style="width:38px;height:38px;border-radius:8px;background:var(--surface-2);border:1px solid var(--border-strong);display:flex;align-items:center;justify-content:center;flex-shrink:0">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polygon points="5 3 19 12 5 21 5 3"/>
              </svg>
            </div>
            <div>
              <div class="ky-eyebrow">AUTONOMOUS MISSION INTAKE</div>
              <h3 style="margin:2px 0 0;font-size:18px">Launch New Mission</h3>
            </div>
          </div>
          <button type="button" class="ky-icon-btn" id="ky-cm-close" aria-label="Close">✕</button>
        </div>

        <div class="ky-modal-body">
          <form id="ky-cm-form" onsubmit="return false;">
            <!-- Target Project -->
            <div style="margin-bottom:16px">
              <label for="ky-cm-project" style="display:block;font-size:12.5px;font-weight:600;margin-bottom:6px">
                Target Project <span style="color:var(--err-fg, #ef4444)">*</span>
              </label>
              <select id="ky-cm-project" class="ky-select" style="width:100%;height:38px;padding:0 12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px">
                ${projectOptionsHtml}
              </select>
            </div>

            <!-- Target Branch & Mode -->
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
              <div>
                <label for="ky-cm-branch" style="display:block;font-size:12.5px;font-weight:600;margin-bottom:6px">
                  Target Branch
                </label>
                <input id="ky-cm-branch" type="text" class="ky-select" value="main" style="width:100%;height:38px;padding:0 12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px;font-family:var(--font-mono)" placeholder="main" />
              </div>
              <div>
                <label for="ky-cm-mode" style="display:block;font-size:12.5px;font-weight:600;margin-bottom:6px">
                  Execution Mode
                </label>
                <select id="ky-cm-mode" class="ky-select" style="width:100%;height:38px;padding:0 12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px">
                  <option value="autopilot">⚡ Autopilot (Autonomous)</option>
                  <option value="approval_required">🛡 Requires Approval</option>
                </select>
              </div>
            </div>

            <!-- Goal Description Textarea -->
            <div style="margin-bottom:20px">
              <label for="ky-cm-prompt" style="display:block;font-size:12.5px;font-weight:600;margin-bottom:6px">
                Goal &amp; Requirements <span style="color:var(--err-fg, #ef4444)">*</span>
              </label>
              <textarea id="ky-cm-prompt" rows="4" autofocus style="width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text-1);font-size:13px;line-height:1.5;outline:none;resize:vertical;font-family:inherit" placeholder="What should Kobits build, refactor, or test? (e.g. 'Add Stripe webhook verification with HMAC-SHA256 and pytest assertions')"></textarea>
              <div style="font-size:11.5px;color:var(--text-3);margin-top:4px">Decomposed into verified tasks across specialist agents (Axiom, Sentinel, Aegis, Atlas).</div>
            </div>

            <!-- Footer Buttons -->
            <div style="display:flex;align-items:center;justify-content:flex-end;gap:10px;padding-top:12px;border-top:1px solid var(--border)">
              <button type="button" class="ky-btn ky-btn-ghost ky-btn-sm" id="ky-cm-cancel">Cancel</button>
              <button type="button" class="ky-btn ky-btn-primary ky-btn-sm" id="ky-cm-submit" style="display:inline-flex;align-items:center;gap:6px">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                <span>Launch Mission</span>
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  `;

  const closeModal = () => {
    host.innerHTML = '';
  };

  document.getElementById('ky-cm-close')?.addEventListener('click', closeModal);
  document.getElementById('ky-cm-cancel')?.addEventListener('click', closeModal);
  document.getElementById('ky-cm-overlay')?.addEventListener('click', (e) => {
    if (e.target && e.target.id === 'ky-cm-overlay') closeModal();
  });

  const projSelect = document.getElementById('ky-cm-project');
  projSelect?.addEventListener('change', () => {
    if (projSelect.value === '__new__') {
      closeModal();
      openNewProjectModal();
    }
  });

  const promptInput = document.getElementById('ky-cm-prompt');
  const submitBtn = document.getElementById('ky-cm-submit');

  const doSubmit = async () => {
    const text = (promptInput?.value || '').trim();
    if (!text) {
      notify('Please enter a goal objective for Kobits.', 'error');
      promptInput?.focus();
      return;
    }

    const selectedPid = projSelect?.value || curPid || 'default';
    const branch = (document.getElementById('ky-cm-branch')?.value || 'main').trim();
    const mode = document.getElementById('ky-cm-mode')?.value || 'autopilot';

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = `<span>⚡ Booting sandbox…</span>`;
    }

    try {
      const title = text.length > 60 ? text.slice(0, 60) + '…' : text;
      const targetPid = (selectedPid && selectedPid !== '__new__' && !selectedPid.startsWith('demo-'))
        ? selectedPid
        : (state.dashboard?.project?.id || 'default');
      const res = await post(`/missions/projects/${encodeURIComponent(targetPid)}/missions`, {
        title: title,
        objective: text,
        branch: branch,
        execution_mode: mode,
        auto_execute: true
      });

      if (res?.project_id) {
        state.projectId = res.project_id;
        localStorage.setItem('kobits.projectId', res.project_id);
      }
      closeModal();
      notify('🚀 Mission launched into cleanroom sandbox!');
      const mId = res.mission_id || res.id;
      if (mId) {
        state.selectedMissionId = mId;
        if (location.hash === `#missions/${mId}`) {
          import('./views/mission.js').then(mod => mod.render(mId));
        } else {
          location.hash = `#missions/${mId}`;
        }
      } else {
        await fetchDashboard(true);
      }
    } catch (err) {
      notify(`Failed to launch mission: ${err.message}`, 'error');
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = `
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          <span>Launch Mission</span>
        `;
      }
    }
  };

  submitBtn?.addEventListener('click', doSubmit);
  promptInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      doSubmit();
    }
  });

  setTimeout(() => promptInput?.focus(), 50);
}
window.openCreateMissionModal = openCreateMissionModal;

// ── Standard Settings Page ─────────────────────────────────────────────────

let _settingsCache = {
  user: null,
  balance: null,
  github: null,
  leads: null,
  leadsFilter: 'all',
};

async function performLogout() {
  try {
    await fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'include' });
  } catch (_) {}
  try {
    localStorage.removeItem('kobits_token');
    localStorage.removeItem('kobits_user');
  } catch (_) {}
  window.location.href = '/portal.html?logout=1#login';
}

async function renderSettingsPage() {
  const view = document.getElementById('view') || document.getElementById('main-content');
  if (!view) return;

  const host = document.getElementById('ky-modal-host');
  if (host) host.innerHTML = '';

  const renderContent = () => {
    const u = _settingsCache.user || {};
    const org = (u.organizations && u.organizations[0]) || { name: 'Personal Workspace', role: 'OWNER' };
    const gh = _settingsCache.github || {};
    const bal = _settingsCache.balance?.balance ?? 50;
    const curTheme = document.documentElement.getAttribute('data-theme') || 'noir';
    const initial = esc((u.full_name || u.email || 'K').charAt(0).toUpperCase());

    view.innerHTML = `
      <div class="ky-settings-container" style="max-width:820px;margin:0 auto;padding:12px 0 60px">
        <!-- Back Navigation & Header -->
        <div style="margin-bottom:24px">
          <a href="#overview" class="ky-btn ky-btn-ghost ky-btn-xs" style="margin-bottom:12px;display:inline-flex;align-items:center;gap:6px">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
            <span>Back to Dashboard</span>
          </a>
          <h1 style="font-size:24px;font-weight:700;margin:0;letter-spacing:-0.02em">Settings</h1>
          <p style="color:var(--text-2);margin:4px 0 0;font-size:13px">Manage your account, integrations, and workspace preferences.</p>
        </div>

        <!-- 1. Profile / Account -->
        <div class="ky-card" style="margin-bottom:20px;padding:24px">
          <div class="ky-card-head" style="margin-bottom:18px">
            <div>
              <div class="ky-section-title" style="font-size:15px;font-weight:600">Profile</div>
              <div style="font-size:12px;color:var(--text-2);margin-top:2px">Your personal account details</div>
            </div>
            <span class="ky-badge ky-badge-done">Active Session</span>
          </div>

          <div style="display:flex;align-items:center;gap:16px;margin-bottom:20px;padding-bottom:20px;border-bottom:1px solid var(--border)">
            <div style="width:48px;height:48px;border-radius:50%;background:var(--surface-2);border:1px solid var(--border-strong);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:18px;font-family:var(--font-mono)">
              ${initial}
            </div>
            <div>
              <div style="font-weight:600;font-size:14px">${esc(u.full_name || 'Kobits User')}</div>
              <div style="font-size:12.5px;color:var(--text-2);font-family:var(--font-mono)">${esc(u.email || 'Loading…')}</div>
            </div>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:18px">
            <div>
              <label for="ky-set-fullname" style="display:block;font-size:12px;font-weight:600;color:var(--text-2);margin-bottom:6px">Display Name</label>
              <input id="ky-set-fullname" type="text" value="${esc(u.full_name || '')}" placeholder="Your full name" style="width:100%;padding:9px 12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13px;color:var(--text-1);outline:none" />
            </div>
            <div>
              <label style="display:block;font-size:12px;font-weight:600;color:var(--text-2);margin-bottom:6px">Email Address</label>
              <input type="text" value="${esc(u.email || '')}" disabled style="width:100%;padding:9px 12px;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;font-size:13px;color:var(--text-3);cursor:not-allowed;font-family:var(--font-mono)" />
            </div>
          </div>

          <div style="display:flex;justify-content:flex-end">
            <button id="ky-set-save-profile" type="button" class="ky-btn ky-btn-primary ky-btn-sm">Save Changes</button>
          </div>
        </div>

        <!-- 2. GitHub Connection -->
        <div class="ky-card" id="ky-card-github" style="margin-bottom:20px;padding:24px">
          <div class="ky-card-head" style="margin-bottom:18px">
            <div>
              <div class="ky-section-title" style="font-size:15px;font-weight:600">GitHub Integration</div>
              <div style="font-size:12px;color:var(--text-2);margin-top:2px">Connect GitHub to sync repositories and push pull requests</div>
            </div>
            ${gh.connected
              ? `<span class="ky-badge ky-badge-done">Connected as @${esc(gh.username)}</span>`
              : `<span class="ky-badge">Not connected</span>`
            }
          </div>

          ${gh.connected ? `
            <div style="padding:14px;background:var(--bg);border:1px solid var(--border);border-radius:6px;display:flex;align-items:center;justify-content:space-between">
              <div>
                <div style="font-weight:600;font-size:13px">Connected as @${esc(gh.username)}</div>
                <div style="font-size:11.5px;color:var(--text-2);margin-top:2px">Kobits has access to clone and push pull requests for your projects.</div>
              </div>
              <button id="ky-set-gh-toggle-update" type="button" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border)">Update Token</button>
            </div>
            <div id="ky-set-gh-update-form" style="display:none;margin-top:14px;padding-top:14px;border-top:1px solid var(--border)">
              <div style="display:grid;grid-template-columns:1fr 1.4fr auto;gap:10px;align-items:end">
                <div>
                  <label style="display:block;font-size:11px;color:var(--text-2);margin-bottom:4px;font-family:var(--font-mono)">GITHUB USERNAME</label>
                  <input id="ky-set-gh-user" type="text" value="${esc(gh.username || '')}" placeholder="username" style="width:100%;padding:9px 11px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13px;color:var(--text-1)" />
                </div>
                <div>
                  <label style="display:block;font-size:11px;color:var(--text-2);margin-bottom:4px;font-family:var(--font-mono)">PERSONAL ACCESS TOKEN</label>
                  <input id="ky-set-gh-token" type="password" placeholder="ghp_••••••••••••••••••••" style="width:100%;padding:9px 11px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13px;color:var(--text-1)" />
                </div>
                <button id="ky-set-gh-connect" type="button" class="ky-btn ky-btn-primary ky-btn-sm">Save Token</button>
              </div>
            </div>
          ` : `
            <div style="display:grid;grid-template-columns:1fr 1.4fr auto;gap:10px;align-items:end">
              <div>
                <label style="display:block;font-size:11px;color:var(--text-2);margin-bottom:4px;font-family:var(--font-mono)">GITHUB USERNAME</label>
                <input id="ky-set-gh-user" type="text" placeholder="e.g. octocat" style="width:100%;padding:9px 11px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13px;color:var(--text-1)" />
              </div>
              <div>
                <label style="display:block;font-size:11px;color:var(--text-2);margin-bottom:4px;font-family:var(--font-mono)">PERSONAL ACCESS TOKEN (PAT)</label>
                <input id="ky-set-gh-token" type="password" placeholder="ghp_••••••••••••••••••••" style="width:100%;padding:9px 11px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13px;color:var(--text-1)" />
              </div>
              <button id="ky-set-gh-connect" type="button" class="ky-btn ky-btn-primary ky-btn-sm">Connect</button>
            </div>
            <div style="margin-top:10px;font-size:11.5px;color:var(--text-3)">
              Need a token? <a href="https://github.com/settings/tokens/new?scopes=repo" target="_blank" style="text-decoration:underline;color:var(--text-1)">Generate a GitHub PAT (select 'repo' scope) ↗</a>
            </div>
          `}
        </div>

        <!-- 3. Early Access Applications / Waitlist Management -->
        <div class="ky-card" id="ky-card-waitlist" style="margin-bottom:20px;padding:24px">
          <div class="ky-card-head" style="margin-bottom:18px">
            <div>
              <div class="ky-section-title" style="font-size:15px;font-weight:600">Early Access Applications</div>
              <div style="font-size:12px;color:var(--text-2);margin-top:2px">Manage incoming applicant requests from the landing page. Approve to grant access with 50 welcome credits.</div>
            </div>
            <div style="display:flex;align-items:center;gap:8px">
              ${(() => {
                const leads = _settingsCache.leads || [];
                const pending = leads.filter(l => (l.status || '').toUpperCase() === 'PENDING').length;
                return pending > 0 
                  ? `<span class="ky-badge ky-badge-warn">${pending} Pending</span>` 
                  : (leads.length > 0 ? `<span class="ky-badge ky-badge-done">${leads.length} Total</span>` : '');
              })()}
              <button id="ky-btn-refresh-leads" type="button" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border);display:inline-flex;align-items:center;gap:4px">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36L21 8"/><path d="M21 3v5h-5"/></svg>
                <span>Refresh</span>
              </button>
            </div>
          </div>

          ${(() => {
            const leads = _settingsCache.leads;
            if (leads === null) {
              return `<div style="padding:24px 0;text-align:center;color:var(--text-3);font-size:13px">Loading early access applications…</div>`;
            }
            if (!leads.length) {
              return `
                <div style="padding:32px 16px;text-align:center;background:var(--bg);border:1px dashed var(--border);border-radius:8px">
                  <div style="font-weight:600;font-size:13.5px;color:var(--text-1);margin-bottom:4px">No early access requests yet</div>
                  <div style="font-size:12px;color:var(--text-3);max-width:440px;margin:0 auto">
                    When visitors click "Apply for Early Access" on the landing page, their applications will show up here. You can approve them with 1 click to provision their 50 credits and get their direct invite link.
                  </div>
                </div>
              `;
            }

            const curFilter = _settingsCache.leadsFilter || 'all';
            const pendingCount = leads.filter(l => (l.status || '').toUpperCase() === 'PENDING').length;
            const approvedCount = leads.filter(l => (l.status || '').toUpperCase() === 'APPROVED').length;
            const rejectedCount = leads.filter(l => (l.status || '').toUpperCase() === 'REJECTED').length;

            const filteredLeads = leads.filter(l => {
              const st = (l.status || '').toUpperCase();
              if (curFilter === 'pending') return st === 'PENDING';
              if (curFilter === 'approved') return st === 'APPROVED';
              if (curFilter === 'rejected') return st === 'REJECTED';
              return true;
            });

            return `
              <div style="display:flex;gap:6px;margin-bottom:14px;border-bottom:1px solid var(--border);padding-bottom:12px">
                <button type="button" class="ky-leads-filter ky-btn ${curFilter === 'all' ? 'ky-btn-primary' : 'ky-btn-ghost'} ky-btn-xs" data-filter="all">All (${leads.length})</button>
                <button type="button" class="ky-leads-filter ky-btn ${curFilter === 'pending' ? 'ky-btn-primary' : 'ky-btn-ghost'} ky-btn-xs" data-filter="pending">Pending (${pendingCount})</button>
                <button type="button" class="ky-leads-filter ky-btn ${curFilter === 'approved' ? 'ky-btn-primary' : 'ky-btn-ghost'} ky-btn-xs" data-filter="approved">Approved (${approvedCount})</button>
                ${rejectedCount > 0 ? `<button type="button" class="ky-leads-filter ky-btn ${curFilter === 'rejected' ? 'ky-btn-primary' : 'ky-btn-ghost'} ky-btn-xs" data-filter="rejected">Declined (${rejectedCount})</button>` : ''}
              </div>

              <div style="display:flex;flex-direction:column;gap:10px">
                ${filteredLeads.map(lead => {
                  const st = (lead.status || 'PENDING').toUpperCase();
                  const isApproved = st === 'APPROVED';
                  const isPending = st === 'PENDING';
                  const inviteUrl = `https://kobits.space/portal.html?email=${encodeURIComponent(lead.email)}#register`;

                  return `
                    <div style="padding:14px 16px;background:var(--bg);border:1px solid var(--border);border-radius:8px;display:flex;flex-direction:column;gap:10px">
                      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
                        <div>
                          <div style="font-weight:600;font-size:13.5px;color:var(--text-1);display:flex;align-items:center;gap:8px">
                            <span>${esc(lead.full_name || 'Applicant')}</span>
                            <span style="font-family:var(--font-mono);font-size:12px;color:var(--text-2);font-weight:400">${esc(lead.email)}</span>
                          </div>
                          <div style="font-size:11.5px;color:var(--text-3);margin-top:3px;display:flex;align-items:center;gap:10px">
                            ${lead.company ? `<span>🏢 ${esc(lead.company)}</span>` : ''}
                            ${lead.github_handle ? `<span>🐙 @${esc(lead.github_handle)}</span>` : ''}
                            <span>⏱ ${timeAgo(lead.created_at)}</span>
                          </div>
                        </div>
                        <div>
                          ${isPending ? `<span class="ky-badge ky-badge-warn">Pending Review</span>` : ''}
                          ${isApproved ? `<span class="ky-badge ky-badge-done">Approved (50 Credits)</span>` : ''}
                          ${st === 'REJECTED' ? `<span class="ky-badge ky-badge-err">Declined</span>` : ''}
                        </div>
                      </div>

                      ${lead.use_case ? `
                        <div style="font-size:12px;color:var(--text-2);background:var(--surface-2);padding:8px 10px;border-radius:6px;line-height:1.4">
                          <span style="font-size:10.5px;text-transform:uppercase;color:var(--text-3);font-family:var(--font-mono);display:block;margin-bottom:2px">Use Case:</span>
                          "${esc(lead.use_case)}"
                        </div>
                      ` : ''}

                      <!-- Actions Row -->
                      <div style="display:flex;align-items:center;justify-content:space-between;padding-top:6px;border-top:1px solid var(--border)">
                        <div style="display:flex;align-items:center;gap:8px">
                          ${isPending ? `
                            <button type="button" class="ky-btn ky-btn-primary ky-btn-xs ky-btn-approve-lead" data-id="${lead.id}" data-email="${esc(lead.email)}">
                              ✓ Approve & Activate (50 Credits)
                            </button>
                            <button type="button" class="ky-btn ky-btn-ghost ky-btn-xs ky-btn-reject-lead" data-id="${lead.id}" style="color:var(--text-3)">
                              Decline
                            </button>
                          ` : ''}

                          ${isApproved ? `
                            <button type="button" class="ky-btn ky-btn-primary ky-btn-xs ky-btn-copy-invite" data-url="${inviteUrl}">
                              Copy Invite Link
                            </button>
                            <a href="${inviteUrl}" target="_blank" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border)">
                              Open Invite ↗
                            </a>
                            <button type="button" class="ky-btn ky-btn-ghost ky-btn-xs ky-btn-reject-lead" data-id="${lead.id}" style="color:var(--text-3)">
                              Revoke
                            </button>
                          ` : ''}

                          ${st === 'REJECTED' ? `
                            <button type="button" class="ky-btn ky-btn-ghost ky-btn-xs ky-btn-approve-lead" data-id="${lead.id}" data-email="${esc(lead.email)}" style="border:1px solid var(--border)">
                              Re-approve
                            </button>
                          ` : ''}
                        </div>

                        <button type="button" class="ky-btn ky-btn-ghost ky-btn-xs ky-btn-delete-lead" data-id="${lead.id}" style="color:rgba(239,68,68,0.7);font-size:11px" title="Delete record">
                          Delete
                        </button>
                      </div>
                    </div>
                  `;
                }).join('')}
              </div>
            `;
          })()}
        </div>

        <!-- 4. Workspace Details -->
        <div class="ky-card" style="margin-bottom:20px;padding:24px">
          <div class="ky-card-head" style="margin-bottom:18px">
            <div>
              <div class="ky-section-title" style="font-size:15px;font-weight:600">Workspace</div>
              <div style="font-size:12px;color:var(--text-2);margin-top:2px">Organization details and mission capacity</div>
            </div>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
            <div style="padding:14px 16px;background:var(--bg);border:1px solid var(--border);border-radius:6px">
              <div class="ky-eyebrow">ORGANIZATION</div>
              <div style="font-weight:600;font-size:14px;margin-top:4px">${esc(org.name || 'Personal Workspace')}</div>
              <div style="font-size:11.5px;color:var(--text-3);margin-top:2px">Role: ${esc((org.role || 'OWNER').toUpperCase())}</div>
            </div>
            <div style="padding:14px 16px;background:var(--bg);border:1px solid var(--border);border-radius:6px;display:flex;align-items:center;justify-content:space-between">
              <div>
                <div class="ky-eyebrow">MISSION CREDITS</div>
                <div style="font-weight:700;font-size:18px;margin-top:2px;font-family:var(--font-mono)">${num(bal)} credits</div>
                <div style="font-size:11.5px;color:var(--text-3)">~${Math.max(0, Math.floor(bal / 10))} autonomous missions</div>
              </div>
              <a href="/pricing.html" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border)">Add Credits →</a>
            </div>
          </div>
        </div>

        <!-- 5. Appearance -->
        <div class="ky-card" style="margin-bottom:20px;padding:24px">
          <div class="ky-card-head" style="margin-bottom:18px">
            <div>
              <div class="ky-section-title" style="font-size:15px;font-weight:600">Appearance</div>
              <div style="font-size:12px;color:var(--text-2);margin-top:2px">Choose your preferred visual theme</div>
            </div>
          </div>

          <div style="display:grid;grid-template-columns:repeat(3, 1fr);gap:12px">
            ${[
              ['noir', 'Dark (Noir)', 'Default high-contrast black'],
              ['carbon', 'Carbon', 'Subtle dark charcoal'],
              ['paper', 'Light (Paper)', 'Clean light theme'],
            ].map(([val, label, desc]) => `
              <button type="button" class="ky-theme-btn" data-theme-val="${val}" style="text-align:left;padding:12px 14px;border-radius:8px;background:var(--bg);border:1px solid ${curTheme === val ? 'var(--text-1)' : 'var(--border)'};cursor:pointer;transition:border-color .15s">
                <div style="font-weight:600;font-size:13px;display:flex;align-items:center;justify-content:space-between">
                  <span>${label}</span>
                  ${curTheme === val ? '<span style="font-size:10px;font-family:var(--font-mono);color:var(--accent)">ACTIVE</span>' : ''}
                </div>
                <div style="font-size:11px;color:var(--text-3);margin-top:4px">${desc}</div>
              </button>
            `).join('')}
          </div>
        </div>

        <!-- 6. Account Actions / Sign Out -->
        <div class="ky-card" style="border-color:rgba(239, 68, 68, 0.25);padding:24px">
          <div class="ky-card-head" style="margin-bottom:0">
            <div>
              <div class="ky-section-title" style="font-size:15px;font-weight:600">Account Session</div>
              <div style="font-size:12px;color:var(--text-2);margin-top:2px">Sign out of your active session on this device</div>
            </div>
            <button id="ky-settings-signout" type="button" class="ky-btn ky-btn-ghost ky-btn-sm" style="border-color:rgba(239,68,68,0.4);color:#ef4444">
              Sign Out
            </button>
          </div>
        </div>
      </div>
    `;

    // Wire Save Profile
    document.getElementById('ky-set-save-profile')?.addEventListener('click', async (ev) => {
      const btn = ev.currentTarget;
      const fullName = document.getElementById('ky-set-fullname')?.value?.trim();
      if (!fullName) return notify('Please enter a display name.', 'error');
      btn.disabled = true;
      btn.textContent = 'Saving…';
      try {
        await fetch('/api/v1/profile/', {
          method: 'PUT',
          credentials: 'include',
          headers: {
            'Content-Type': 'application/json',
            ...(localStorage.getItem('kobits_token') ? { 'Authorization': `Bearer ${localStorage.getItem('kobits_token')}` } : {}),
          },
          body: JSON.stringify({ full_name: fullName }),
        });
        if (_settingsCache.user) _settingsCache.user.full_name = fullName;
        notify('Profile updated.', 'info');
        renderContent();
      } catch (e) {
        notify('Failed to update profile.', 'error');
      } finally {
        btn.disabled = false;
        btn.textContent = 'Save Changes';
      }
    });

    // Wire GitHub Connect / Update
    document.getElementById('ky-set-gh-toggle-update')?.addEventListener('click', () => {
      const form = document.getElementById('ky-set-gh-update-form');
      if (form) form.style.display = form.style.display === 'none' ? 'block' : 'none';
    });

    document.getElementById('ky-set-gh-connect')?.addEventListener('click', async (ev) => {
      const btn = ev.currentTarget;
      const username = document.getElementById('ky-set-gh-user')?.value?.trim();
      const token = document.getElementById('ky-set-gh-token')?.value?.trim();
      if (!username || !token) return notify('Enter both GitHub username and Personal Access Token.', 'error');
      btn.disabled = true;
      btn.textContent = 'Connecting…';
      try {
        await post('/github/connect', { username, token });
        _settingsCache.github = { connected: true, username };
        notify(`Connected GitHub account @${username}`, 'info');
        renderContent();
      } catch (e) {
        notify(e.message || 'GitHub connection failed.', 'error');
        btn.disabled = false;
        btn.textContent = 'Connect';
      }
    });

    // Wire Early Access Leads Interactions
    view.querySelectorAll('.ky-leads-filter').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        _settingsCache.leadsFilter = ev.currentTarget.getAttribute('data-filter') || 'all';
        renderContent();
      });
    });

    document.getElementById('ky-btn-refresh-leads')?.addEventListener('click', async (ev) => {
      const btn = ev.currentTarget;
      btn.disabled = true;
      try {
        _settingsCache.leads = await get('/waitlist/leads');
        notify('Applications list refreshed.', 'info');
        renderContent();
      } catch (_) {
        notify('Failed to refresh applications.', 'error');
      } finally {
        btn.disabled = false;
      }
    });

    view.querySelectorAll('.ky-btn-approve-lead').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        const b = ev.currentTarget;
        const id = b.getAttribute('data-id');
        const email = b.getAttribute('data-email');
        b.disabled = true;
        b.textContent = 'Approving…';
        try {
          const res = await post(`/waitlist/leads/${id}/approve`);
          if (_settingsCache.leads) {
            const item = _settingsCache.leads.find(l => l.id === id);
            if (item) item.status = 'APPROVED';
          }
          const inviteUrl = res?.lead?.invite_url || `https://kobits.space/portal.html?email=${encodeURIComponent(email)}#register`;
          if (navigator.clipboard) {
            await navigator.clipboard.writeText(inviteUrl);
            notify(`Approved ${email}! Invite link copied to clipboard.`, 'info');
          } else {
            notify(`Approved ${email}! 50 credits granted.`, 'info');
          }
          renderContent();
        } catch (err) {
          notify(err.message || 'Failed to approve lead.', 'error');
          b.disabled = false;
          b.textContent = '✓ Approve & Activate (50 Credits)';
        }
      });
    });

    view.querySelectorAll('.ky-btn-copy-invite').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        const b = ev.currentTarget;
        const url = b.getAttribute('data-url');
        if (!url) return;
        try {
          await navigator.clipboard.writeText(url);
          const oldText = b.textContent;
          b.textContent = '✓ Copied!';
          notify('Invite link copied to clipboard!', 'info');
          setTimeout(() => { b.textContent = oldText; }, 2000);
        } catch (_) {
          prompt('Copy this invite link:', url);
        }
      });
    });

    view.querySelectorAll('.ky-btn-reject-lead').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        const b = ev.currentTarget;
        const id = b.getAttribute('data-id');
        b.disabled = true;
        try {
          await post(`/waitlist/leads/${id}/reject`);
          if (_settingsCache.leads) {
            const item = _settingsCache.leads.find(l => l.id === id);
            if (item) item.status = 'REJECTED';
          }
          notify('Lead marked as declined.', 'info');
          renderContent();
        } catch (err) {
          notify(err.message || 'Failed to update lead.', 'error');
          b.disabled = false;
        }
      });
    });

    view.querySelectorAll('.ky-btn-delete-lead').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        const id = ev.currentTarget.getAttribute('data-id');
        if (!confirm('Are you sure you want to delete this applicant record?')) return;
        try {
          await fetch(`/api/v1/waitlist/leads/${id}`, {
            method: 'DELETE',
            credentials: 'include',
            headers: {
              ...(localStorage.getItem('kobits_token') ? { 'Authorization': `Bearer ${localStorage.getItem('kobits_token')}` } : {})
            }
          });
          if (_settingsCache.leads) {
            _settingsCache.leads = _settingsCache.leads.filter(l => l.id !== id);
          }
          notify('Record removed.', 'info');
          renderContent();
        } catch (_) {
          notify('Failed to delete lead.', 'error');
        }
      });
    });

    // Wire Theme buttons
    view.querySelectorAll('.ky-theme-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const val = btn.getAttribute('data-theme-val');
        document.documentElement.setAttribute('data-theme', val);
        localStorage.setItem('kobits.theme', val);
        renderContent();
      });
    });

    // Wire Sign Out
    document.getElementById('ky-settings-signout')?.addEventListener('click', performLogout);
  };

  // Render immediately with cached data
  renderContent();

  // Refresh in background
  try {
    const [u, bal, gh, leads] = await Promise.all([
      get('/auth/me').catch(() => _settingsCache.user),
      get('/billing/balance').catch(() => _settingsCache.balance),
      get('/github/status').catch(() => _settingsCache.github),
      get('/waitlist/leads').catch(() => _settingsCache.leads),
    ]);
    if (u) _settingsCache.user = u;
    if (bal) _settingsCache.balance = bal;
    if (gh) _settingsCache.github = gh;
    if (leads) _settingsCache.leads = leads;
    renderContent();
  } catch (_) {}
}

function openSettingsModal() {
  location.hash = '#settings';
  renderSettingsPage();
}

async function openGitHubModal() {
  const host = document.getElementById('ky-modal-host');
  if (!host) return;

  let gh = _settingsCache.github;
  if (!gh || gh.connected === undefined) {
    try {
      gh = await get('/github/status');
      _settingsCache.github = gh;
    } catch (_) {
      const savedUser = typeof localStorage !== 'undefined' ? localStorage.getItem('kobits.gh_user') : null;
      gh = savedUser ? { connected: true, username: savedUser } : { connected: false };
      _settingsCache.github = gh;
    }
  }

  const isConnected = Boolean(gh && gh.connected && gh.username);

  host.innerHTML = `
    <div class="ky-modal-overlay" id="ky-modal-bg">
      <div class="ky-modal" style="max-width:540px" role="dialog" aria-modal="true">
        <div class="ky-modal-head" style="align-items:flex-start">
          <div style="display:flex;align-items:center;gap:12px">
            <div style="width:38px;height:38px;border-radius:8px;background:var(--surface-2);border:1px solid var(--border-strong);display:flex;align-items:center;justify-content:center;flex-shrink:0">
              <svg width="22" height="22" fill="currentColor" viewBox="0 0 24 24"><path fill-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" clip-rule="evenodd"/></svg>
            </div>
            <div>
              <div class="ky-eyebrow">GIT HOST PROVIDER</div>
              <h3 style="margin:2px 0 0;font-size:18px">GitHub Integration</h3>
            </div>
          </div>
          <button type="button" class="ky-icon-btn" id="ky-modal-close" aria-label="Close">✕</button>
        </div>

        <div class="ky-modal-body">
          <p style="margin:0 0 16px;color:var(--text-2);font-size:13px;line-height:1.5">
            Connect your GitHub account with a Personal Access Token (PAT). This enables Kobits autonomous agents to clone repositories, execute cleanroom sandbox builds, and publish Pull Requests.
          </p>

          ${isConnected ? `
            <div style="padding:14px 16px;background:var(--surface-2);border:1px solid var(--border-strong);border-radius:8px;margin-bottom:18px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
              <div>
                <div style="display:flex;align-items:center;gap:8px">
                  <span class="ky-badge ky-badge-done" style="font-size:11px">Connected</span>
                  <strong style="font-size:14px;color:var(--text-1)">@${esc(gh.username)}</strong>
                </div>
                <div style="font-size:12px;color:var(--text-3);margin-top:4px">Access token active with repository permissions.</div>
              </div>
              <div style="display:flex;align-items:center;gap:8px">
                <button id="ky-modal-gh-toggle" type="button" class="ky-btn ky-btn-ghost ky-btn-xs" style="border:1px solid var(--border)">
                  Update Token
                </button>
                <button id="ky-modal-gh-disconnect" type="button" class="ky-btn ky-btn-ghost ky-btn-xs" style="color:var(--err-fg, #ef4444);border:1px solid var(--border)">
                  Disconnect
                </button>
              </div>
            </div>
          ` : ''}

          <div id="ky-modal-gh-form" style="${isConnected ? 'display:none;' : ''}">
            <div style="margin-bottom:14px">
              <label for="ky-m-gh-user" style="display:block;font-size:12px;font-weight:600;color:var(--text-2);margin-bottom:5px;font-family:var(--font-mono)">
                GITHUB USERNAME <span style="color:var(--err-fg, #ef4444)">*</span>
              </label>
              <input id="ky-m-gh-user" type="text" value="${esc(gh.username || '')}" placeholder="e.g. your-github-username" style="width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13px;color:var(--text-1);outline:none" />
            </div>

            <div style="margin-bottom:14px">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:5px;flex-wrap:wrap;gap:4px">
                <label for="ky-m-gh-token" style="font-size:12px;font-weight:600;color:var(--text-2);font-family:var(--font-mono)">
                  PERSONAL ACCESS TOKEN (PAT) <span style="color:var(--err-fg, #ef4444)">*</span>
                </label>
                <a href="https://github.com/settings/tokens/new?scopes=repo" target="_blank" rel="noopener noreferrer" style="font-size:11.5px;color:var(--text-1);text-decoration:underline">
                  Generate token with repo scope ↗
                </a>
              </div>
              <input id="ky-m-gh-token" type="password" placeholder="ghp_••••••••••••••••••••" style="width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13px;color:var(--text-1);outline:none;font-family:var(--font-mono)" />
              <div style="font-size:11.5px;color:var(--text-3);margin-top:5px">
                Requires standard <code>repo</code> scope to clone and create Pull Requests.
              </div>
            </div>

            <div style="display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-top:20px;padding-top:14px;border-top:1px solid var(--border)">
              <button id="ky-modal-gh-cancel" type="button" class="ky-btn ky-btn-ghost ky-btn-sm">Cancel</button>
              <button id="ky-modal-gh-submit" type="button" class="ky-btn ky-btn-primary ky-btn-sm" style="padding:8px 18px">
                ${isConnected ? 'Save New Token' : 'Connect GitHub'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;

  // Attach event handlers
  document.getElementById('ky-modal-bg')?.addEventListener('click', (e) => {
    if (e.target.id === 'ky-modal-bg') host.innerHTML = '';
  });
  document.getElementById('ky-modal-close')?.addEventListener('click', () => { host.innerHTML = ''; });
  document.getElementById('ky-modal-gh-cancel')?.addEventListener('click', () => { host.innerHTML = ''; });

  document.getElementById('ky-modal-gh-toggle')?.addEventListener('click', () => {
    const f = document.getElementById('ky-modal-gh-form');
    if (f) f.style.display = f.style.display === 'none' ? 'block' : 'none';
  });

  document.getElementById('ky-modal-gh-disconnect')?.addEventListener('click', async () => {
    try {
      await post('/github/disconnect', {});
    } catch (_) {}
    if (typeof localStorage !== 'undefined') localStorage.removeItem('kobits.gh_user');
    _settingsCache.github = { connected: false };
    updateTopBarGitHubStatus({ connected: false });
    notify('GitHub account disconnected.', 'info');
    openGitHubModal();
    if (location.hash === '#overview' || !location.hash) fetchDashboard(true);
  });

  const submitBtn = document.getElementById('ky-modal-gh-submit');
  submitBtn?.addEventListener('click', async () => {
    const username = document.getElementById('ky-m-gh-user')?.value?.trim();
    const token = document.getElementById('ky-m-gh-token')?.value?.trim();
    if (!username || !token) {
      notify('Enter both GitHub username and Personal Access Token.', 'error');
      return;
    }
    submitBtn.disabled = true;
    submitBtn.textContent = 'Connecting…';
    try {
      await post('/github/connect', { username, token });
      _settingsCache.github = { connected: true, username };
      if (typeof localStorage !== 'undefined') localStorage.setItem('kobits.gh_user', username);
      updateTopBarGitHubStatus({ connected: true, username });
      notify(`Connected to GitHub as @${username}!`, 'info');
      host.innerHTML = '';
      if (location.hash === '#overview' || !location.hash) fetchDashboard(true);
    } catch (e) {
      notify(e.message || 'GitHub connection failed.', 'error');
      submitBtn.disabled = false;
      submitBtn.textContent = isConnected ? 'Save New Token' : 'Connect GitHub';
    }
  });
}
window.openGitHubModal = openGitHubModal;

// ── Real-World SaaS Product Onboarding Walkthrough (Linear/Vercel Style) ────

function openOnboardingWalkthrough(step = 1) {
  const host = document.getElementById('ky-modal-host');
  if (!host) return;

  const totalSteps = 4;
  let content = '';

  if (step === 1) {
    content = `
      <div class="ky-onboarding-step-badge">Step 1 of 4 • Multi-Agent Hierarchy</div>
      <h3 class="ky-onboarding-title">Meet Your Autonomous Engineering Fleet</h3>
      <p class="ky-onboarding-desc">
        Kobits replaces single-turn AI chat with an orchestrated multi-agent cleanroom. 
        Specialized agents plan, implement, verify, and security-audit code in parallel topological workstreams.
      </p>
      
      <div class="ky-onboarding-card-grid" style="grid-template-columns: repeat(2, 1fr); gap: 10px;">
        <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:12px 14px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            ${agentGlyph('Axiom', 14)}
            <strong style="color:#fff;font-size:12px">Axiom (Orchestrator)</strong>
          </div>
          <div style="font-size:11px;color:#a1a1aa;line-height:1.4">Decomposes engineering goals into topological dependency DAGs and monitors convergence.</div>
        </div>

        <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:12px 14px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            ${agentGlyph('Sentinel', 14)}
            <strong style="color:#fff;font-size:12px">Sentinel (QA & TDD)</strong>
          </div>
          <div style="font-size:11px;color:#a1a1aa;line-height:1.4">Authors comprehensive pytest suites and verifies 100% test pass rates in cleanroom venvs.</div>
        </div>

        <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:12px 14px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            ${agentGlyph('Forge', 14)}
            <strong style="color:#fff;font-size:12px">Forge (Backend Core)</strong>
          </div>
          <div style="font-size:11px;color:#a1a1aa;line-height:1.4">Implements performant Python/FastAPI services, AST syntax trees, and data structures.</div>
        </div>

        <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:12px 14px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            ${agentGlyph('Aegis', 14)}
            <strong style="color:#fff;font-size:12px">Aegis (Security Auditor)</strong>
          </div>
          <div style="font-size:11px;color:#a1a1aa;line-height:1.4">Scans for OWASP vulnerabilities, token leaks, and privilege escalations before commits.</div>
        </div>
      </div>
    `;
  } else if (step === 2) {
    content = `
      <div class="ky-onboarding-step-badge">Step 2 of 4 • Isolation</div>
      <h3 class="ky-onboarding-title">5-Layer Cleanroom Sandbox Isolation</h3>
      <p class="ky-onboarding-desc">
        Zero repository pollution. Kobits guarantees that no broken or untested code ever touches your primary branch.
      </p>

      <div style="background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.1);border-radius:10px;padding:16px 20px;margin-bottom:16px">
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;font-family:var(--font-mono);font-size:11px">
          <span style="color:#a1a1aa;background:rgba(255,255,255,0.06);padding:6px 10px;border-radius:6px">1. Host Repo</span>
          <span style="color:#71717a">→</span>
          <span style="color:#fff;background:rgba(255,255,255,0.1);padding:6px 10px;border-radius:6px;border:1px solid rgba(255,255,255,0.2)">2. Git Worktree</span>
          <span style="color:#71717a">→</span>
          <span style="color:#a1a1aa;background:rgba(255,255,255,0.06);padding:6px 10px;border-radius:6px">3. Compiler AST</span>
          <span style="color:#71717a">→</span>
          <span style="color:#a1a1aa;background:rgba(255,255,255,0.06);padding:6px 10px;border-radius:6px">4. Pytest Pass</span>
          <span style="color:#71717a">→</span>
          <span style="color:#fff;background:rgba(255,255,255,0.12);padding:6px 10px;border-radius:6px">5. Verified Merge</span>
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:10px;font-size:12.5px;color:#a1a1aa;line-height:1.5">
        <div style="display:flex;gap:10px;align-items:flex-start">
          <span style="color:#fff;font-family:var(--font-mono);font-weight:bold">•</span>
          <span><strong>Isolated Worktrees:</strong> Every task executes in a dedicated git worktree branch, leaving your working directory untouched.</span>
        </div>
        <div style="display:flex;gap:10px;align-items:flex-start">
          <span style="color:#fff;font-family:var(--font-mono);font-weight:bold">•</span>
          <span><strong>Full AST Verification:</strong> Syntax errors and broken imports are caught locally by language servers before tests run.</span>
        </div>
        <div style="display:flex;gap:10px;align-items:flex-start">
          <span style="color:#fff;font-family:var(--font-mono);font-weight:bold">•</span>
          <span><strong>Unified Diff Inspector:</strong> Review syntax-highlighted diffs and apply changes with one click.</span>
        </div>
      </div>
    `;
  } else if (step === 3) {
    content = `
      <div class="ky-onboarding-step-badge">Step 3 of 4 • Real-Time Control</div>
      <h3 class="ky-onboarding-title">Mid-Flight Steering & The Dual-Loop Engine</h3>
      <p class="ky-onboarding-desc">
        Never wait for a mission to complete or restart from scratch to correct course. 
        You can inject constraints into agents while they are working.
      </p>

      <div style="background:#050505;border:1px solid rgba(255,255,255,0.15);border-radius:8px;padding:14px 16px;margin-bottom:16px">
        <div style="font-family:var(--font-mono);font-size:10px;color:#a1a1aa;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px;display:flex;align-items:center;gap:6px">
          <span style="width:6px;height:6px;border-radius:50%;background:#fff;box-shadow:0 0 6px #fff"></span>
          Mid-Flight Steering Telemetry Bar
        </div>
        <div style="display:flex;gap:10px;align-items:center;background:rgba(255,255,255,0.03);border:1px dashed rgba(255,255,255,0.2);padding:8px 12px;border-radius:6px">
          <span style="font-family:var(--font-mono);font-size:11px;color:#a1a1aa">>_ STEER:</span>
          <span style="font-family:var(--font-sans);font-size:12px;color:#fff">"Add Redis cache fallback if database replica has high replication lag"</span>
          <span style="margin-left:auto;font-family:var(--font-mono);font-size:10px;background:#fff;color:#000;padding:2px 8px;border-radius:4px;font-weight:600">STEERED</span>
        </div>
      </div>

      <p style="font-size:12.5px;color:#a1a1aa;line-height:1.5">
        Whenever a mission is executing, the <strong>Steering Bar</strong> appears at the bottom of the Overview tab. 
        Type any adjustment, press Enter, and Axiom will dynamically re-route subtasks in real time.
      </p>
    `;
  } else if (step === 4) {
    content = `
      <div class="ky-onboarding-step-badge">Step 4 of 4 • Get Started</div>
      <h3 class="ky-onboarding-title">Launch Your First Engineering Mission</h3>
      <p class="ky-onboarding-desc">
        Choose a real-world starter mission template below or type your own goal to experience autonomous engineering:
      </p>

      <div class="ky-onboarding-card-grid" style="grid-template-columns:1fr;gap:10px;margin-bottom:16px">
        <div class="ky-starter-card" data-prompt="Implement Token-Bucket rate limiting middleware in FastAPI with Redis/memory backing and comprehensive pytest suite.">
          <div class="ky-starter-title">
            <span>FastAPI Ingress Rate-Limiting</span>
            <span style="font-family:var(--font-mono);font-size:10.5px;color:#a1a1aa">Security & QA</span>
          </div>
          <div class="ky-starter-prompt">"Implement Token-Bucket rate limiting middleware in FastAPI with Redis/memory backing and comprehensive pytest suite."</div>
        </div>

        <div class="ky-starter-card" data-prompt="Build an append-only write-ahead log (WAL) with deterministic replay, crash recovery semantics, and pytest concurrency checks.">
          <div class="ky-starter-title">
            <span>ACID Write-Ahead Log (WAL) Storage</span>
            <span style="font-family:var(--font-mono);font-size:10.5px;color:#a1a1aa">Core Backend</span>
          </div>
          <div class="ky-starter-prompt">"Build an append-only write-ahead log (WAL) with deterministic replay, crash recovery semantics, and pytest concurrency checks."</div>
        </div>

        <div class="ky-starter-card" data-prompt="Analyze existing SQLite database models, add composite B-Tree indexes, and author zero-downtime migrations.">
          <div class="ky-starter-title">
            <span>Database Indexing & ACID Optimization</span>
            <span style="font-family:var(--font-mono);font-size:10.5px;color:#a1a1aa">Database Engine</span>
          </div>
          <div class="ky-starter-prompt">"Analyze existing SQLite database models, add composite B-Tree indexes, and author zero-downtime migrations."</div>
        </div>
      </div>
    `;
  }

  const dots = Array.from({ length: totalSteps }, (_, i) => {
    return `<div class="ky-onboarding-dot ${i + 1 === step ? 'active' : ''}"></div>`;
  }).join('');

  host.innerHTML = `
    <div class="ky-onboarding-overlay">
      <div class="ky-onboarding-modal" role="dialog" aria-modal="true" aria-label="Kobits Product Onboarding">
        <div class="ky-onboarding-header">
          <div class="ky-onboarding-progress">${dots}</div>
          <button id="ky-onboarding-skip" class="ky-btn ky-btn-ghost ky-btn-xs" style="font-family:var(--font-mono);font-size:11px;color:#71717a">
            Skip Tour
          </button>
        </div>
        
        <div class="ky-onboarding-body">${content}</div>

        <div class="ky-onboarding-footer">
          <div>
            ${step > 1 ? `<button id="ky-onboarding-prev" class="ky-btn ky-btn-ghost ky-btn-sm" style="font-family:var(--font-mono);font-size:11.5px">← Back</button>` : ''}
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            ${step < totalSteps ? `
              <button id="ky-onboarding-next" class="ky-btn ky-btn-primary ky-btn-sm" style="font-family:var(--font-mono);font-size:11.5px">
                Next →
              </button>
            ` : `
              <button id="ky-onboarding-finish" class="ky-btn ky-btn-primary ky-btn-sm" style="font-family:var(--font-mono);font-size:11.5px">
                Enter Workspace
              </button>
            `}
          </div>
        </div>
      </div>
    </div>
  `;

  // Attach event handlers
  document.getElementById('ky-onboarding-skip')?.addEventListener('click', () => {
    localStorage.setItem('kobits.onboarded', 'true');
    host.innerHTML = '';
  });

  document.getElementById('ky-onboarding-prev')?.addEventListener('click', () => {
    openOnboardingWalkthrough(step - 1);
  });

  document.getElementById('ky-onboarding-next')?.addEventListener('click', () => {
    openOnboardingWalkthrough(step + 1);
  });

  document.getElementById('ky-onboarding-finish')?.addEventListener('click', () => {
    localStorage.setItem('kobits.onboarded', 'true');
    host.innerHTML = '';
    notify('Welcome to Kobits Workspace! Ready to launch missions.', 'info');
  });

  if (step === 4) {
    host.querySelectorAll('.ky-starter-card').forEach(card => {
      card.addEventListener('click', () => {
        const prompt = card.getAttribute('data-prompt');
        localStorage.setItem('kobits.onboarded', 'true');
        host.innerHTML = '';
        state.tab = 'overview';
        location.hash = '#overview';
        renderDashboard();
        setTimeout(() => {
          const p = document.getElementById('ky-mission-prompt');
          if (p) {
            p.value = prompt;
            p.focus();
            p.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
          notify('Mission template loaded into prompt console.', 'info');
        }, 120);
      });
    });
  }
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

const initialHash = getEffectiveTriadRoute();
if (['overview', 'milestones', 'deliverables', 'team', 'reports'].includes(initialHash)) {
  state.tab = initialHash;
}

initTopBar();
fetchDashboard();
initLiveWebSocket();

if (isTriadSurfaceRoute(initialHash)) {
  // Dispatched cleanly via fetchDashboard()
} else if (initialHash === 'settings') {
  renderSettingsPage();
} else if (initialHash === 'new-project') {
  renderNewProjectPage();
} else if (initialHash.startsWith('missions/') || initialHash.startsWith('mission/')) {
  const parts = initialHash.split('/');
  const mId = parts[1];
  if (mId) {
    import('./views/mission.js').then(mod => mod.render(mId));
  }
} else if (initialHash === 'missions') {
  import('./views/missions.js').then(mod => mod.render());
}

// Auto-trigger onboarding walkthrough on first visit or when ?onboarding=true is present
try {
  const urlParams = new URLSearchParams(window.location.search);
  if (!localStorage.getItem('kobits.onboarded') || urlParams.get('onboarding') === 'true') {
    setTimeout(() => openOnboardingWalkthrough(1), 350);
  }
} catch {}

setInterval(() => {
  fetchDashboard(true);
}, 8000);



window.kySetPrompt = function(txt) {
  const el = document.getElementById('ky-mission-prompt');
  if (el) {
    el.value = txt;
    el.focus();
  }
};
