import { get, post } from '../api.js';
import { getThemePref, setThemePref } from '../core.js';
import { errorBox, esc, skeleton, titleCase } from '../ui.js';

export async function render() {
  const view = document.getElementById('view');
  view.innerHTML = `<div class="page fade-in">
    <h1 style="margin-bottom: 24px;">Settings</h1>
    
    <div class="stack" style="display: flex; flex-direction: column; gap: 16px; max-width: 600px;">
      <!-- GitHub Integration Card -->
      <div class="card" id="github-card">
        <div class="card-head" style="display:flex;align-items:center;justify-content:space-between">
          <h2>GitHub Integration</h2>
          <span id="github-badge" class="badge" style="font-size:11px;padding:2px 8px;border-radius:99px;background:rgba(255,255,255,0.08);color:var(--text-3,#a1a1aa)">Checking…</span>
        </div>
        <div class="card-body" id="github-body">
          ${skeleton(2)}
        </div>
      </div>

      <div class="card" id="health-card">
        <div class="card-head"><h2>LLM Provider</h2></div>
        <div class="card-body" id="health-body">
          ${skeleton(2)}
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h2>Appearance</h2></div>
        <div class="card-body">
          <div style="display: flex; gap: 8px;" id="theme-buttons">
            <button class="btn theme-btn" data-theme="system">System</button>
            <button class="btn theme-btn" data-theme="light">Light</button>
            <button class="btn theme-btn" data-theme="dark">Dark</button>
          </div>
        </div>
      </div>

      <!-- Early Access Applications (Founder Admin) -->
      <div class="card" id="waitlist-card">
        <div class="card-head" style="display:flex;align-items:center;justify-content:space-between">
          <h2>Early Access Applications</h2>
          <span id="waitlist-count" class="badge" style="font-size:11px;padding:2px 8px;border-radius:99px;background:rgba(255,255,255,0.08);color:var(--text-3,#a1a1aa)">Founder Admin</span>
        </div>
        <div class="card-body" id="waitlist-body">
          ${skeleton(2)}
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h2>About Kobits</h2></div>
        <div class="card-body" style="line-height: 1.5;">
          <p>Autonomous multi-agent engineering system. Every change is verified in a sandbox before it touches your repo.</p>
          <div style="margin-top: 12px; display: flex; align-items: center; justify-content: space-between;">
            <span class="faint mono">v1.0.0</span>
            <a href="#" class="btn btn-ghost btn-sm" style="text-decoration: none;">View documentation &rarr;</a>
          </div>
        </div>
      </div>
    </div>
  </div>`;

  setupThemeButtons();
  await loadGitHub();
  await loadHealth();
  await loadWaitlist();
}

function setupThemeButtons() {
  const current = getThemePref();
  const btns = document.querySelectorAll('.theme-btn');
  
  function updateState(theme) {
    btns.forEach(b => {
      if (b.dataset.theme === theme) {
        b.classList.remove('btn-ghost');
      } else {
        b.classList.add('btn-ghost');
      }
    });
  }

  updateState(current);

  btns.forEach(btn => {
    btn.addEventListener('click', (e) => {
      const theme = e.currentTarget.dataset.theme;
      setThemePref(theme);
      updateState(theme);
    });
  });
}

async function loadHealth() {
  const body = document.getElementById('health-body');
  try {
    const health = await get('/provider/health');
    const isOk = health.configured && health.available;
    
    let warningHtml = '';
    if (!health.configured) {
      warningHtml = `<div style="margin-top: 12px; padding: 12px; background: #fff3cd; color: #856404; border-radius: 4px; font-size: 14px; border: 1px solid #ffeeba;">
        Provider not configured. Set LLM_PROVIDER and API key in .env
      </div>`;
    }

    body.innerHTML = `
      <div style="display: flex; align-items: center; gap: 12px; font-size: 15px;">
        <div style="width: 10px; height: 10px; border-radius: 50%; background-color: ${isOk ? 'var(--success, #2BA877)' : 'var(--error, #E86C8D)'};"></div>
        <div style="font-weight: 600;">${esc(titleCase(health.provider || 'Unknown Provider'))}</div>
        <div class="faint mono" style="margin-left: auto;">${esc(health.model || '')}</div>
      </div>
      ${warningHtml}
    `;
  } catch (e) {
    body.innerHTML = errorBox(e);
  }
}

async function loadGitHub() {
  const badge = document.getElementById('github-badge');
  const body = document.getElementById('github-body');
  try {
    const status = await get('/github/status');
    if (status && status.connected) {
      if (badge) {
        badge.textContent = 'Connected';
        badge.style.background = 'rgba(43,168,119,0.15)';
        badge.style.color = '#2BA877';
      }
      body.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
          <div style="display:flex;align-items:center;gap:8px">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/></svg>
            <span style="font-weight:600;font-size:14px">@${esc(status.username || 'user')}</span>
          </div>
          <button id="btn-reconnect-gh" class="btn btn-ghost btn-sm" style="font-size:11px">Reconnect</button>
        </div>
        <p class="faint" style="font-size:12.5px;margin:0">Kobits has permission to clone worktrees, run tests, and open Pull Requests on your repositories.</p>
      `;
      document.getElementById('btn-reconnect-gh')?.addEventListener('click', () => showGitHubForm(body, badge, status.username));
    } else {
      if (badge) {
        badge.textContent = 'Not Connected';
        badge.style.background = 'rgba(255,255,255,0.06)';
        badge.style.color = '#71717a';
      }
      showGitHubForm(body, badge);
    }
  } catch (e) {
    if (badge) badge.textContent = 'Offline';
    showGitHubForm(body, badge);
  }
}

function showGitHubForm(body, badge, prefillUser = '') {
  body.innerHTML = `
    <div style="font-size:13px;line-height:1.5;margin-bottom:14px;color:var(--text-2,#a1a1aa)">
      Connect a GitHub Personal Access Token (PAT) so Kobits can create branches, commit sandbox fixes, and open Pull Requests.
    </div>
    <form id="gh-connect-form" style="display:flex;flex-direction:column;gap:10px">
      <div>
        <label style="display:block;font-size:11px;font-family:var(--font-mono);color:var(--text-3,#71717a);margin-bottom:4px;text-transform:uppercase">GitHub Username</label>
        <input id="gh-input-username" type="text" placeholder="e.g. kobitscreation-dev" value="${esc(prefillUser)}" required
          style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#fff;font-size:13px;font-family:inherit">
      </div>
      <div>
        <div style="display:flex;justify-content:space-between;margin-bottom:4px">
          <label style="font-size:11px;font-family:var(--font-mono);color:var(--text-3,#71717a);text-transform:uppercase">Personal Access Token (classic)</label>
          <a href="https://github.com/settings/tokens/new?scopes=repo,workflow&description=Kobits+Autonomous+Engineer" target="_blank" style="font-size:11px;color:var(--accent,#fff);text-decoration:none">Generate on GitHub &rarr;</a>
        </div>
        <input id="gh-input-token" type="password" placeholder="ghp_xxxxxxxxxxxxxxxxxxxx" required
          style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#fff;font-size:13px;font-family:var(--font-mono)">
      </div>
      <div id="gh-connect-error" style="display:none;color:#E86C8D;font-size:12px;margin-top:2px"></div>
      <div style="display:flex;gap:8px;margin-top:4px">
        <button id="btn-gh-submit" type="submit" class="btn btn-primary btn-sm" style="font-weight:600">Connect GitHub</button>
      </div>
    </form>
  `;

  document.getElementById('gh-connect-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.getElementById('btn-gh-submit');
    const errBox = document.getElementById('gh-connect-error');
    const username = document.getElementById('gh-input-username')?.value.trim();
    const token = document.getElementById('gh-input-token')?.value.trim();

    if (!username || !token) return;
    if (btn) { btn.disabled = true; btn.textContent = 'Connecting…'; }
    if (errBox) errBox.style.display = 'none';

    try {
      await post('/github/connect', { username, token });
      await loadGitHub();
    } catch (err) {
      if (errBox) {
        errBox.textContent = err.message || 'Failed to connect. Verify token permissions.';
        errBox.style.display = 'block';
      }
      if (btn) { btn.disabled = false; btn.textContent = 'Connect GitHub'; }
    }
  });
}

async function loadWaitlist() {
  const body = document.getElementById('waitlist-body');
  const countBadge = document.getElementById('waitlist-count');
  if (!body) return;
  try {
    const leads = await get('/waitlist/leads');
    if (!leads || !Array.isArray(leads) || leads.length === 0) {
      body.innerHTML = `<p class="faint" style="font-size:13px;line-height:1.5">No early access applications received yet. Visitors can apply via the landing page modal, and you can approve them here.</p>`;
      if (countBadge) countBadge.textContent = '0 Leads';
      return;
    }
    const pendingCount = leads.filter(l => l.status === 'PENDING').length;
    if (countBadge) countBadge.textContent = `${pendingCount} Pending`;

    body.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:10px">
        ${leads.map(l => {
          const isPending = l.status === 'PENDING';
          const host = window.location.origin;
          const inviteUrl = `${host}/portal.html?email=${encodeURIComponent(l.email)}#register`;
          return `
          <div style="display:flex;flex-direction:column;gap:6px;padding:12px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:8px">
            <div style="display:flex;justify-content:space-between;align-items:flex-start">
              <div>
                <div style="font-weight:600;font-size:13px;color:#fff">${esc(l.full_name || 'Anonymous Applicant')}</div>
                <div class="faint mono" style="font-size:11px">${esc(l.email)} ${l.company ? '• ' + esc(l.company) : ''} ${l.github_handle ? '• @' + esc(l.github_handle) : ''}</div>
              </div>
              <div>
                ${isPending ? `
                  <button class="btn btn-primary btn-sm btn-approve-lead" data-id="${esc(l.id)}" style="background:#22c55e;border-color:#22c55e;color:#000;font-weight:600;padding:3px 10px;font-size:11px">Approve & Invite</button>
                ` : `
                  <span class="badge" style="background:rgba(34,197,94,0.15);color:#4ade80;font-size:11px;padding:2px 8px;border-radius:99px;font-weight:600">Approved</span>
                `}
              </div>
            </div>
            ${l.use_case ? `<div style="font-size:12px;color:var(--text-2,#d4d4d8);background:rgba(255,255,255,0.02);padding:6px 8px;border-radius:4px">${esc(l.use_case)}</div>` : ''}
            ${!isPending ? `
              <div style="display:flex;align-items:center;justify-content:space-between;margin-top:4px;padding:4px 8px;background:rgba(34,197,94,0.05);border-radius:4px">
                <span class="mono faint" style="font-size:10px">Workspace ready (50 credits)</span>
                <button class="btn btn-ghost btn-sm" style="font-size:10px;padding:2px 6px" onclick="navigator.clipboard.writeText('${inviteUrl}'); alert('Copied invite link: ${inviteUrl}')">Copy Invite Link</button>
              </div>
            ` : ''}
          </div>
        `}).join('')}
      </div>
    `;

    body.querySelectorAll('.btn-approve-lead').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        const id = e.target.getAttribute('data-id');
        e.target.disabled = true;
        e.target.textContent = 'Approving…';
        try {
          await post(`/waitlist/leads/${id}/approve`, {});
          await loadWaitlist();
        } catch (err) {
          alert('Failed to approve lead: ' + (err.message || err));
          e.target.disabled = false;
          e.target.textContent = 'Approve & Invite';
        }
      });
    });
  } catch (err) {
    body.innerHTML = `<p class="faint" style="font-size:13px">Could not load waitlist: ${esc(err.message)}</p>`;
  }
}

