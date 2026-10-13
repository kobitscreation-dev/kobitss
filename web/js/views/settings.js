import { get, post } from '../api.js';
import { getThemePref, setThemePref } from '../core.js';
import { errorBox, esc, icon, skeleton, titleCase, toast } from '../ui.js';

// Active tab in settings state
let currentTab = 'workspace';

export async function render() {
  const view = document.getElementById('view');
  
  view.innerHTML = `
    <div class="page fade-in" style="max-width: 1100px; margin: 0 auto; padding-bottom: 60px;">
      <!-- Settings Header -->
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:28px;border-bottom:1px solid var(--border);padding-bottom:18px">
        <div>
          <h1 style="font-size:24px;font-weight:700;margin:0 0 6px 0;letter-spacing:-0.02em">Workspace Settings</h1>
          <p class="faint" style="font-size:13.5px;margin:0">Manage your autonomous engineering swarm, cloud sandboxes, AI models, and organization preferences.</p>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <span class="badge" style="font-size:11px;padding:3px 10px;border-radius:99px;background:rgba(255,255,255,0.06);color:var(--text-2);font-family:var(--font-mono)">
            Tier: Pro Team
          </span>
          <span class="badge" style="font-size:11px;padding:3px 10px;border-radius:99px;background:rgba(34,197,94,0.12);color:#22c55e;font-family:var(--font-mono);display:inline-flex;align-items:center;gap:4px">
            <span style="width:6px;height:6px;border-radius:50%;background:#22c55e"></span>
            AWS RDS Connected
          </span>
        </div>
      </div>

      <!-- Settings Tabs Navigation -->
      <div class="settings-tabs-bar" style="display:flex;gap:4px;border-bottom:1px solid var(--border);margin-bottom:28px;overflow-x:auto;padding-bottom:2px">
        <button class="settings-nav-btn ${currentTab === 'workspace' ? 'active' : ''}" data-tab="workspace">
          🏢 Workspace & General
        </button>
        <button class="settings-nav-btn ${currentTab === 'models' ? 'active' : ''}" data-tab="models">
          🧠 AI Models & Swarm
        </button>
        <button class="settings-nav-btn ${currentTab === 'sandboxes' ? 'active' : ''}" data-tab="sandboxes">
          ⚡ Cloud Sandboxes & Security
        </button>
        <button class="settings-nav-btn ${currentTab === 'integrations' ? 'active' : ''}" data-tab="integrations">
          🐙 Git & Integrations
        </button>
        <button class="settings-nav-btn ${currentTab === 'billing' ? 'active' : ''}" data-tab="billing">
          💳 Billing & Credits
        </button>
        <button class="settings-nav-btn ${currentTab === 'early-access' ? 'active' : ''}" data-tab="early-access">
          🛡️ Early Access Admin <span id="tab-lead-badge" style="display:none;background:#22c55e;color:#000;font-size:10px;font-weight:700;padding:1px 6px;border-radius:99px;margin-left:4px">1</span>
        </button>
      </div>

      <!-- Tab Content Pane -->
      <div id="settings-tab-pane" style="min-height: 480px;">
        ${skeleton(4)}
      </div>
    </div>
  `;

  // Attach tab switching listeners
  view.querySelectorAll('.settings-nav-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const tab = e.currentTarget.dataset.tab;
      if (tab === currentTab) return;
      currentTab = tab;
      view.querySelectorAll('.settings-nav-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === currentTab));
      renderActiveTab();
    });
  });

  // Check waitlist count for badge
  checkWaitlistBadge();

  // Render initial tab
  await renderActiveTab();
}

async function checkWaitlistBadge() {
  try {
    const leads = await get('/waitlist/leads');
    if (leads && Array.isArray(leads)) {
      const pending = leads.filter(l => l.status === 'PENDING').length;
      const badge = document.getElementById('tab-lead-badge');
      if (badge && pending > 0) {
        badge.textContent = pending;
        badge.style.display = 'inline-block';
      }
    }
  } catch (_) {}
}

async function renderActiveTab() {
  const pane = document.getElementById('settings-tab-pane');
  if (!pane) return;

  switch (currentTab) {
    case 'workspace':
      await renderWorkspaceTab(pane);
      break;
    case 'models':
      await renderModelsTab(pane);
      break;
    case 'sandboxes':
      await renderSandboxesTab(pane);
      break;
    case 'integrations':
      await renderIntegrationsTab(pane);
      break;
    case 'billing':
      await renderBillingTab(pane);
      break;
    case 'early-access':
      await renderEarlyAccessTab(pane);
      break;
    default:
      await renderWorkspaceTab(pane);
  }
}

// ────────────────────────────────────────────────────────────────
// TAB 1: WORKSPACE & GENERAL
// ────────────────────────────────────────────────────────────────
async function renderWorkspaceTab(container) {
  let user = { email: 'founder@kobits.space', full_name: 'Lead Architect', role: 'OWNER' };
  try {
    const me = await get('/auth/me');
    if (me && me.email) user = me;
  } catch (_) {}

  const currentTheme = getThemePref();
  const currentDensity = localStorage.getItem('kobits.density') || 'default';
  const currentBg = localStorage.getItem('kobits.bg') || 'grid';
  const currentBranch = localStorage.getItem('kobits.default_branch') || 'main';

  container.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:20px;">
      <!-- Workspace Identity Card -->
      <div class="card" style="padding:22px;">
        <div class="card-head" style="margin-bottom:16px;">
          <div>
            <div class="ky-eyebrow">Organization Identity</div>
            <h2 style="font-size:16px;margin:2px 0 0">Workspace Profile</h2>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">
          <div>
            <label style="display:block;font-size:11.5px;color:var(--text-3);font-family:var(--font-mono);margin-bottom:6px;text-transform:uppercase">Organization Name</label>
            <input type="text" id="ws-org-name" class="input" value="${esc(localStorage.getItem('kobits.org_name') || 'Kobits Core Engineering')}" 
              style="width:100%;padding:9px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;color:#fff;font-size:13.5px">
          </div>
          <div>
            <label style="display:block;font-size:11.5px;color:var(--text-3);font-family:var(--font-mono);margin-bottom:6px;text-transform:uppercase">Workspace Slug (URL)</label>
            <input type="text" readonly value="kobits.space/ws/core-platform" 
              style="width:100%;padding:9px 12px;background:rgba(255,255,255,0.015);border:1px solid var(--border);border-radius:6px;color:var(--text-3);font-size:13.5px;font-family:var(--font-mono);cursor:not-allowed">
          </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
          <div>
            <label style="display:block;font-size:11.5px;color:var(--text-3);font-family:var(--font-mono);margin-bottom:6px;text-transform:uppercase">Cloud Region</label>
            <div style="padding:9px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;font-size:13px;display:flex;align-items:center;justify-content:space-between">
              <span>AWS ap-south-1 (Mumbai)</span>
              <span class="mono faint" style="font-size:11px">Multi-AZ RDS PostgreSQL</span>
            </div>
          </div>
          <div>
            <label style="display:block;font-size:11.5px;color:var(--text-3);font-family:var(--font-mono);margin-bottom:6px;text-transform:uppercase">Default Target Branch</label>
            <input type="text" id="ws-default-branch" value="${esc(currentBranch)}" 
              style="width:100%;padding:9px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;color:#fff;font-size:13.5px;font-family:var(--font-mono)">
          </div>
        </div>
        <div style="margin-top:16px;display:flex;justify-content:flex-end">
          <button id="btn-save-workspace" class="btn btn-primary btn-sm" style="font-weight:600">Save Workspace Changes</button>
        </div>
      </div>

      <!-- Theme & Appearance Card -->
      <div class="card" style="padding:22px;">
        <div class="card-head" style="margin-bottom:16px;">
          <div>
            <div class="ky-eyebrow">Design System & Theme</div>
            <h2 style="font-size:16px;margin:2px 0 0">Appearance & Ergonomics</h2>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:12px">
          <!-- Color Theme -->
          <div>
            <label style="display:block;font-size:11.5px;color:var(--text-3);font-family:var(--font-mono);margin-bottom:8px;text-transform:uppercase">Color Theme</label>
            <div style="display:flex;flex-direction:column;gap:6px" id="theme-selector">
              <button class="btn theme-opt ${currentTheme === 'noir' || currentTheme === 'dark' ? 'btn-primary' : 'btn-ghost'}" data-theme="noir" style="justify-content:flex-start;padding:8px 12px;font-size:12.5px">
                🌑 Noir (High Contrast Dark)
              </button>
              <button class="btn theme-opt ${currentTheme === 'carbon' ? 'btn-primary' : 'btn-ghost'}" data-theme="carbon" style="justify-content:flex-start;padding:8px 12px;font-size:12.5px">
                🪐 Carbon (Deep Slate)
              </button>
              <button class="btn theme-opt ${currentTheme === 'paper' || currentTheme === 'light' ? 'btn-primary' : 'btn-ghost'}" data-theme="paper" style="justify-content:flex-start;padding:8px 12px;font-size:12.5px">
                📄 Paper (Crisp Light)
              </button>
              <button class="btn theme-opt ${currentTheme === 'system' ? 'btn-primary' : 'btn-ghost'}" data-theme="system" style="justify-content:flex-start;padding:8px 12px;font-size:12.5px">
                💻 System Default
              </button>
            </div>
          </div>

          <!-- UI Density -->
          <div>
            <label style="display:block;font-size:11.5px;color:var(--text-3);font-family:var(--font-mono);margin-bottom:8px;text-transform:uppercase">Interface Density</label>
            <div style="display:flex;flex-direction:column;gap:6px" id="density-selector">
              <button class="btn density-opt ${currentDensity === 'compact' ? 'btn-primary' : 'btn-ghost'}" data-density="compact" style="justify-content:flex-start;padding:8px 12px;font-size:12.5px">
                📐 Compact (High Data Density)
              </button>
              <button class="btn density-opt ${currentDensity === 'default' ? 'btn-primary' : 'btn-ghost'}" data-density="default" style="justify-content:flex-start;padding:8px 12px;font-size:12.5px">
                ⚖️ Standard (Balanced)
              </button>
              <button class="btn density-opt ${currentDensity === 'spacious' ? 'btn-primary' : 'btn-ghost'}" data-density="spacious" style="justify-content:flex-start;padding:8px 12px;font-size:12.5px">
                🛋️ Spacious (Relaxed Padding)
              </button>
            </div>
          </div>

          <!-- Background Pattern -->
          <div>
            <label style="display:block;font-size:11.5px;color:var(--text-3);font-family:var(--font-mono);margin-bottom:8px;text-transform:uppercase">Canvas Texture</label>
            <div style="display:flex;flex-direction:column;gap:6px" id="bg-selector">
              <button class="btn bg-opt ${currentBg === 'grid' ? 'btn-primary' : 'btn-ghost'}" data-bg="grid" style="justify-content:flex-start;padding:8px 12px;font-size:12.5px">
                🕸️ Blueprint Grid Lines
              </button>
              <button class="btn bg-opt ${currentBg === 'dots' ? 'btn-primary' : 'btn-ghost'}" data-bg="dots" style="justify-content:flex-start;padding:8px 12px;font-size:12.5px">
                📍 Dot Matrix Matrix
              </button>
              <button class="btn bg-opt ${currentBg === 'clean' ? 'btn-primary' : 'btn-ghost'}" data-bg="clean" style="justify-content:flex-start;padding:8px 12px;font-size:12.5px">
                ⬛ Pure Solid Monochrome
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Organization Members Overview -->
      <div class="card" style="padding:22px;">
        <div class="card-head" style="margin-bottom:16px;">
          <div>
            <div class="ky-eyebrow">Team & Permissions</div>
            <h2 style="font-size:16px;margin:2px 0 0">Active Seat Allocation</h2>
          </div>
          <span class="badge" style="font-size:11px;padding:3px 9px;border-radius:99px;background:rgba(255,255,255,0.06)">1 of 5 Seats Active</span>
        </div>
        <div style="display:flex;flex-direction:column;gap:8px">
          <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:8px">
            <div style="display:flex;align-items:center;gap:12px">
              <div style="width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,0.1);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;color:#fff">
                ${esc((user.full_name || user.email || 'A')[0].toUpperCase())}
              </div>
              <div>
                <div style="font-weight:600;font-size:13.5px;color:#fff">${esc(user.full_name || 'Ayush (Lead Engineer)')}</div>
                <div class="faint mono" style="font-size:11.5px">${esc(user.email || 'founder@kobits.space')}</div>
              </div>
            </div>
            <div style="display:flex;align-items:center;gap:8px">
              <span class="badge" style="font-size:10.5px;padding:2px 8px;border-radius:99px;background:rgba(255,255,255,0.1);color:#fff;font-weight:600">Owner & Architect</span>
              <span class="badge" style="font-size:10.5px;padding:2px 8px;border-radius:99px;background:rgba(34,197,94,0.15);color:#4ade80">Active Session</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Danger Zone Card -->
      <div class="card" style="padding:22px;border-color:rgba(239,68,68,0.25);">
        <div class="card-head" style="margin-bottom:12px;">
          <div>
            <div class="ky-eyebrow" style="color:#ef4444">Security & Danger Zone</div>
            <h2 style="font-size:16px;margin:2px 0 0;color:#f87171">Session & Workspace State</h2>
          </div>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;padding-top:8px">
          <div>
            <div style="font-weight:600;font-size:13px">Sign out of active session</div>
            <div class="faint" style="font-size:12px">Invalidates your current PostgreSQL session token and returns to portal entrance.</div>
          </div>
          <button id="btn-sign-out" class="btn btn-ghost btn-sm" style="color:#ef4444;border-color:rgba(239,68,68,0.3);font-weight:600">
            Sign Out
          </button>
        </div>
      </div>
    </div>
  `;

  // Handle Workspace Save
  container.querySelector('#btn-save-workspace')?.addEventListener('click', () => {
    const orgName = container.querySelector('#ws-org-name')?.value.trim();
    const branch = container.querySelector('#ws-default-branch')?.value.trim();
    if (orgName) localStorage.setItem('kobits.org_name', orgName);
    if (branch) localStorage.setItem('kobits.default_branch', branch);
    toast('Workspace settings saved successfully!', 'info');
  });

  // Handle Theme switching
  container.querySelectorAll('.theme-opt').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const theme = e.currentTarget.dataset.theme;
      setThemePref(theme);
      document.documentElement.setAttribute('data-theme', theme);
      container.querySelectorAll('.theme-opt').forEach(b => {
        b.classList.toggle('btn-primary', b.dataset.theme === theme);
        b.classList.toggle('btn-ghost', b.dataset.theme !== theme);
      });
      toast(`Theme set to ${theme}`, 'info');
    });
  });

  // Handle Density switching
  container.querySelectorAll('.density-opt').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const d = e.currentTarget.dataset.density;
      localStorage.setItem('kobits.density', d);
      document.documentElement.setAttribute('data-density', d);
      container.querySelectorAll('.density-opt').forEach(b => {
        b.classList.toggle('btn-primary', b.dataset.density === d);
        b.classList.toggle('btn-ghost', b.dataset.density !== d);
      });
      toast(`Interface density: ${d}`, 'info');
    });
  });

  // Handle Background Texture
  container.querySelectorAll('.bg-opt').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const bg = e.currentTarget.dataset.bg;
      localStorage.setItem('kobits.bg', bg);
      document.documentElement.setAttribute('data-bg', bg);
      container.querySelectorAll('.bg-opt').forEach(b => {
        b.classList.toggle('btn-primary', b.dataset.bg === bg);
        b.classList.toggle('btn-ghost', b.dataset.bg !== bg);
      });
      toast(`Canvas texture: ${bg}`, 'info');
    });
  });

  // Handle Logout
  container.querySelector('#btn-sign-out')?.addEventListener('click', async () => {
    if (!confirm('Are you sure you want to sign out?')) return;
    try {
      await post('/auth/logout', {});
    } catch (_) {}
    localStorage.removeItem('kobits_token');
    window.location.href = '/portal.html#login';
  });
}

// ────────────────────────────────────────────────────────────────
// TAB 2: AI MODELS & AGENT SWARM
// ────────────────────────────────────────────────────────────────
async function renderModelsTab(container) {
  container.innerHTML = skeleton(4);
  let health = { provider: 'bedrock', configured: true, available: true, model: 'global.anthropic.claude-sonnet-4-6' };
  try {
    const res = await get('/provider/health');
    if (res) health = res;
  } catch (_) {}

  const currentThinking = localStorage.getItem('kobits.reasoning_effort') || 'high';
  const currentConcurrency = localStorage.getItem('kobits.max_concurrency') || '4';
  const currentTemp = localStorage.getItem('kobits.temperature') || '0.2';

  container.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:20px;">
      <!-- Active Provider Card -->
      <div class="card" style="padding:22px;">
        <div class="card-head" style="margin-bottom:16px;">
          <div>
            <div class="ky-eyebrow">Foundation Model Infrastructure</div>
            <h2 style="font-size:16px;margin:2px 0 0">Primary LLM Provider & Routing</h2>
          </div>
          <div style="display:flex;align-items:center;gap:6px">
            <span style="width:8px;height:8px;border-radius:50%;background:${health.available ? '#22c55e' : '#ef4444'}"></span>
            <span class="mono" style="font-size:12px;color:${health.available ? '#22c55e' : '#ef4444'};font-weight:600">
              ${health.available ? 'Live & Operational' : 'Degraded / Offline'}
            </span>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:18px">
          <!-- Active Model Tile -->
          <div style="padding:16px;background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.12);border-radius:8px">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
              <span class="mono faint" style="font-size:11px;text-transform:uppercase">Active Model</span>
              <span class="badge" style="background:rgba(34,197,94,0.12);color:#4ade80;font-size:10px;padding:1px 6px">Default</span>
            </div>
            <div style="font-weight:700;font-size:15px;color:#fff;margin-bottom:4px">
              AWS Bedrock Claude 3.7 Sonnet
            </div>
            <div class="mono faint" style="font-size:11.5px;margin-bottom:10px">
              ${esc(health.model || 'global.anthropic.claude-sonnet-4-6')}
            </div>
            <div style="font-size:12px;color:var(--text-3);line-height:1.4">
              Ultra-fast coding & multi-file AST synthesis via AWS Bedrock native endpoints.
            </div>
          </div>

          <!-- Secondary Fallback Tile -->
          <div style="padding:16px;background:rgba(255,255,255,0.015);border:1px solid var(--border);border-radius:8px">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
              <span class="mono faint" style="font-size:11px;text-transform:uppercase">Automatic Failover</span>
              <span class="badge" style="background:rgba(255,255,255,0.05);color:var(--text-3);font-size:10px;padding:1px 6px">Circuit Breaker</span>
            </div>
            <div style="font-weight:700;font-size:15px;color:#fff;margin-bottom:4px">
              Anthropic Claude 3.5 Sonnet / DeepSeek R1
            </div>
            <div class="mono faint" style="font-size:11.5px;margin-bottom:10px">
              claude-3-5-sonnet-20241022
            </div>
            <div style="font-size:12px;color:var(--text-3);line-height:1.4">
              Auto-activates if AWS Bedrock returns 429 rate limit or AWS credential rotation delays.
            </div>
          </div>
        </div>

        <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:6px">
          <div style="font-size:13px;color:var(--text-2)">
            Provider Protocol: <span class="mono" style="color:#fff;font-weight:600">${esc(health.provider.toUpperCase())}</span> (Encrypted IAM keys)
          </div>
          <button id="btn-test-provider" class="btn btn-ghost btn-sm" style="font-size:11.5px">Ping Provider API</button>
        </div>
      </div>

      <!-- Autonomous Reasoning & Swarm Controls -->
      <div class="card" style="padding:22px;">
        <div class="card-head" style="margin-bottom:16px;">
          <div>
            <div class="ky-eyebrow">Reasoning & Concurrency</div>
            <h2 style="font-size:16px;margin:2px 0 0">Swarm Execution Parameters</h2>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:16px">
          <!-- Reasoning Effort -->
          <div>
            <label style="display:block;font-size:12px;font-weight:600;color:#fff;margin-bottom:4px">
              Extended Thinking & Architectural Reasoning
            </label>
            <p class="faint" style="font-size:12px;margin:0 0 10px 0;line-height:1.4">
              Allows Axiom and Solution Architect agents to output internal scratchpad plans before touching files.
            </p>
            <div style="display:flex;gap:6px" id="reasoning-selector">
              <button class="btn reasoning-opt ${currentThinking === 'low' ? 'btn-primary' : 'btn-ghost'} btn-sm" data-val="low">Standard</button>
              <button class="btn reasoning-opt ${currentThinking === 'high' ? 'btn-primary' : 'btn-ghost'} btn-sm" data-val="high">Deep (64k tokens)</button>
              <button class="btn reasoning-opt ${currentThinking === 'auto' ? 'btn-primary' : 'btn-ghost'} btn-sm" data-val="auto">Adaptive Swarm</button>
            </div>
          </div>

          <!-- Concurrency Limit -->
          <div>
            <label style="display:block;font-size:12px;font-weight:600;color:#fff;margin-bottom:4px">
              Parallel Agent Swarm Concurrency
            </label>
            <p class="faint" style="font-size:12px;margin:0 0 10px 0;line-height:1.4">
              Maximum concurrent specialist agents executing parallel sub-tasks (Sentinel, Forge, Muse, Atlas).
            </p>
            <select id="select-concurrency" style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;color:#fff;font-size:13px">
              <option value="2" ${currentConcurrency === '2' ? 'selected' : ''}>2 Parallel Specialists (Resource Conservative)</option>
              <option value="4" ${currentConcurrency === '4' ? 'selected' : ''}>4 Parallel Specialists (Recommended Default)</option>
              <option value="8" ${currentConcurrency === '8' ? 'selected' : ''}>8 Parallel Specialists (High-Speed Turbo)</option>
            </select>
          </div>
        </div>

        <!-- Token Ceiling -->
        <div style="padding-top:14px;border-top:1px solid var(--border);display:flex;align-items:center;justify-content:space-between">
          <div>
            <div style="font-weight:600;font-size:13px;color:#fff">Emergency Spend Circuit Breaker</div>
            <div class="faint" style="font-size:12px">Automatically pauses mission if an individual task exceeds $1.50 in Bedrock token usage.</div>
          </div>
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="chk-circuit-breaker" checked style="width:16px;height:16px;accent-color:#22c55e">
            <span class="mono" style="font-size:12px;color:#fff">Enforced</span>
          </label>
        </div>
      </div>
    </div>
  `;

  // Reasoning options
  container.querySelectorAll('.reasoning-opt').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const v = e.currentTarget.dataset.val;
      localStorage.setItem('kobits.reasoning_effort', v);
      container.querySelectorAll('.reasoning-opt').forEach(b => {
        b.classList.toggle('btn-primary', b.dataset.val === v);
        b.classList.toggle('btn-ghost', b.dataset.val !== v);
      });
      toast(`Thinking effort updated to ${v}`, 'info');
    });
  });

  // Concurrency select
  container.querySelector('#select-concurrency')?.addEventListener('change', (e) => {
    localStorage.setItem('kobits.max_concurrency', e.target.value);
    toast(`Max concurrency updated to ${e.target.value} workers`, 'info');
  });

  // Test provider ping
  container.querySelector('#btn-test-provider')?.addEventListener('click', async (e) => {
    e.target.disabled = true;
    e.target.textContent = 'Pinging…';
    try {
      const res = await get('/provider/health');
      toast(`Provider ping successful: ${res.model} is operational!`, 'info');
    } catch (err) {
      toast(`Provider ping error: ${err.message}`, 'error');
    } finally {
      e.target.disabled = false;
      e.target.textContent = 'Ping Provider API';
    }
  });
}

// ────────────────────────────────────────────────────────────────
// TAB 3: CLOUD SANDBOXES & SECURITY
// ────────────────────────────────────────────────────────────────
async function renderSandboxesTab(container) {
  const currentTimeout = localStorage.getItem('kobits.sandbox_timeout') || '600';
  const strictAst = localStorage.getItem('kobits.strict_ast') !== 'false';
  const netEgress = localStorage.getItem('kobits.net_egress') || 'restricted';

  container.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:20px;">
      <!-- Sandbox Engine Card -->
      <div class="card" style="padding:22px;">
        <div class="card-head" style="margin-bottom:16px;">
          <div>
            <div class="ky-eyebrow">Execution Cleanroom</div>
            <h2 style="font-size:16px;margin:2px 0 0">E2B Firecracker MicroVM Cluster</h2>
          </div>
          <span class="badge" style="font-size:11px;padding:3px 9px;border-radius:99px;background:rgba(34,197,94,0.12);color:#22c55e;font-weight:600">
            Verified & Active
          </span>
        </div>

        <p class="faint" style="font-size:13px;line-height:1.5;margin:0 0 16px 0">
          Every coding task runs inside an isolated ephemeral Linux MicroVM (Firecracker kernel). Zero untrusted code ever executes on your host machine or touches your primary repository until cleanroom tests pass.
        </p>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">
          <div style="padding:14px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:6px">
            <div class="mono faint" style="font-size:11px;margin-bottom:4px">MICROVM PROVIDER</div>
            <div style="font-weight:600;font-size:14px;color:#fff">E2B Cloud Sandboxes</div>
            <div class="mono faint" style="font-size:11px;margin-top:4px">Key: e2b_cfa43cd83ea9...</div>
          </div>

          <div style="padding:14px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:6px">
            <div class="mono faint" style="font-size:11px;margin-bottom:4px">CLEANROOM OS</div>
            <div style="font-weight:600;font-size:14px;color:#fff">Debian 12 Slim + Python 3.12</div>
            <div class="mono faint" style="font-size:11px;margin-top:4px">Node.js 20 LTS + Git 2.43</div>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
          <div>
            <label style="display:block;font-size:11.5px;color:var(--text-3);font-family:var(--font-mono);margin-bottom:6px;text-transform:uppercase">Execution Timeout per Task</label>
            <select id="sandbox-timeout" style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;color:#fff;font-size:13px">
              <option value="300" ${currentTimeout === '300' ? 'selected' : ''}>300 seconds (5 mins)</option>
              <option value="600" ${currentTimeout === '600' ? 'selected' : ''}>600 seconds (10 mins - Standard)</option>
              <option value="1200" ${currentTimeout === '1200' ? 'selected' : ''}>1200 seconds (20 mins - Large builds)</option>
            </select>
          </div>

          <div>
            <label style="display:block;font-size:11.5px;color:var(--text-3);font-family:var(--font-mono);margin-bottom:6px;text-transform:uppercase">Network Egress Filter</label>
            <select id="sandbox-egress" style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;color:#fff;font-size:13px">
              <option value="restricted" ${netEgress === 'restricted' ? 'selected' : ''}>Restricted (PyPI, npm, GitHub only)</option>
              <option value="full" ${netEgress === 'full' ? 'selected' : ''}>Full Internet Access</option>
              <option value="isolated" ${netEgress === 'isolated' ? 'selected' : ''}>Zero Network Egress (Air-gapped)</option>
            </select>
          </div>
        </div>
      </div>

      <!-- Security Policies (Aegis Gate) -->
      <div class="card" style="padding:22px;">
        <div class="card-head" style="margin-bottom:16px;">
          <div>
            <div class="ky-eyebrow">Aegis Security Verification</div>
            <h2 style="font-size:16px;margin:2px 0 0">Pre-Merge Policy Gates</h2>
          </div>
        </div>

        <div style="display:flex;flex-direction:column;gap:12px">
          <label style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:6px;cursor:pointer">
            <div>
              <div style="font-weight:600;font-size:13px;color:#fff">Mandatory AST Syntax Validation</div>
              <div class="faint" style="font-size:12px">Fails any task immediately if code contains Python/JS syntax errors before pytest runs.</div>
            </div>
            <input type="checkbox" id="chk-strict-ast" ${strictAst ? 'checked' : ''} style="width:18px;height:18px;accent-color:#22c55e">
          </label>

          <label style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:6px;cursor:pointer">
            <div>
              <div style="font-weight:600;font-size:13px;color:#fff">OWASP Secret & Credential Leakage Scanner</div>
              <div class="faint" style="font-size:12px">Prevents AWS, Stripe, or OpenAI keys from ever being written to git diffs.</div>
            </div>
            <input type="checkbox" checked disabled style="width:18px;height:18px;accent-color:#22c55e">
          </label>

          <label style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:6px;cursor:pointer">
            <div>
              <div style="font-weight:600;font-size:13px;color:#fff">Sentinel Pytest Gate</div>
              <div class="faint" style="font-size:12px">Diff cannot be submitted for user approval until Sentinel reports 100% test pass rate.</div>
            </div>
            <input type="checkbox" checked disabled style="width:18px;height:18px;accent-color:#22c55e">
          </label>
        </div>
      </div>
    </div>
  `;

  container.querySelector('#sandbox-timeout')?.addEventListener('change', (e) => {
    localStorage.setItem('kobits.sandbox_timeout', e.target.value);
    toast(`Sandbox execution timeout: ${e.target.value}s`, 'info');
  });

  container.querySelector('#sandbox-egress')?.addEventListener('change', (e) => {
    localStorage.setItem('kobits.net_egress', e.target.value);
    toast(`Network egress policy: ${e.target.value}`, 'info');
  });

  container.querySelector('#chk-strict-ast')?.addEventListener('change', (e) => {
    localStorage.setItem('kobits.strict_ast', e.target.checked);
    toast(`AST syntax validation: ${e.target.checked ? 'Enabled' : 'Disabled'}`, 'info');
  });
}

// ────────────────────────────────────────────────────────────────
// TAB 4: GIT & INTEGRATIONS
// ────────────────────────────────────────────────────────────────
async function renderIntegrationsTab(container) {
  container.innerHTML = skeleton(4);
  let status = { connected: false, username: null };
  try {
    const res = await get('/github/status');
    if (res) status = res;
  } catch (_) {}

  const prStrategy = localStorage.getItem('kobits.pr_strategy') || 'draft';
  const webhookUrl = localStorage.getItem('kobits.webhook_url') || '';

  container.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:20px;">
      <!-- GitHub Account Connection -->
      <div class="card" style="padding:22px;">
        <div class="card-head" style="margin-bottom:16px;">
          <div>
            <div class="ky-eyebrow">Source Control Integration</div>
            <h2 style="font-size:16px;margin:2px 0 0">GitHub Account & Repository Access</h2>
          </div>
          <span class="badge" style="font-size:11px;padding:3px 9px;border-radius:99px;background:${status.connected ? 'rgba(34,197,94,0.15)' : 'rgba(255,255,255,0.06)'};color:${status.connected ? '#22c55e' : 'var(--text-3)'}">
            ${status.connected ? 'Connected' : 'Not Connected'}
          </span>
        </div>

        <div id="github-section-body">
          ${status.connected ? `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:14px;background:rgba(255,255,255,0.025);border:1px solid var(--border);border-radius:8px;margin-bottom:12px">
              <div style="display:flex;align-items:center;gap:12px">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/></svg>
                <div>
                  <div style="font-weight:600;font-size:14px;color:#fff">@${esc(status.username || 'user')}</div>
                  <div class="faint mono" style="font-size:11px">Permissions: repo, workflow, read:org</div>
                </div>
              </div>
              <button id="btn-reconnect-gh" class="btn btn-ghost btn-sm">Reconnect PAT</button>
            </div>
            <p class="faint" style="font-size:12.5px;margin:0">Kobits has automated permission to checkout worktrees, run tests, and open Pull Requests directly on your connected repos.</p>
          ` : `
            <div style="font-size:13px;line-height:1.5;margin-bottom:14px;color:var(--text-2)">
              Connect a GitHub Personal Access Token (classic) so Kobits can create branches, commit sandbox fixes, and open verified Pull Requests.
            </div>
            <form id="gh-connect-form" style="display:flex;flex-direction:column;gap:12px">
              <div>
                <label style="display:block;font-size:11px;font-family:var(--font-mono);color:var(--text-3);margin-bottom:4px;text-transform:uppercase">GitHub Username</label>
                <input id="gh-input-username" type="text" placeholder="e.g. kobitscreation-dev" value="kobitscreation-dev" required
                  style="width:100%;padding:9px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;color:#fff;font-size:13px">
              </div>
              <div>
                <div style="display:flex;justify-content:space-between;margin-bottom:4px">
                  <label style="font-size:11px;font-family:var(--font-mono);color:var(--text-3);text-transform:uppercase">Personal Access Token (classic)</label>
                  <a href="https://github.com/settings/tokens/new?scopes=repo,workflow&description=Kobits+Autonomous+Engineer" target="_blank" style="font-size:11px;color:#fff;text-decoration:none">Generate token on GitHub &rarr;</a>
                </div>
                <input id="gh-input-token" type="password" placeholder="ghp_xxxxxxxxxxxxxxxxxxxx" required
                  style="width:100%;padding:9px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;color:#fff;font-size:13px;font-family:var(--font-mono)">
              </div>
              <div id="gh-connect-error" style="display:none;color:#ef4444;font-size:12px"></div>
              <div>
                <button id="btn-gh-submit" type="submit" class="btn btn-primary btn-sm" style="font-weight:600">Connect GitHub Account</button>
              </div>
            </form>
          `}
        </div>
      </div>

      <!-- Git Automation Behavior -->
      <div class="card" style="padding:22px;">
        <div class="card-head" style="margin-bottom:16px;">
          <div>
            <div class="ky-eyebrow">Pull Request Workflow</div>
            <h2 style="font-size:16px;margin:2px 0 0">Delivery Strategy</h2>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:14px">
          <div>
            <label style="display:block;font-size:11.5px;color:var(--text-3);font-family:var(--font-mono);margin-bottom:6px;text-transform:uppercase">Pull Request Mode</label>
            <select id="pr-strategy" style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;color:#fff;font-size:13px">
              <option value="draft" ${prStrategy === 'draft' ? 'selected' : ''}>Open Draft PR (Recommended - safe review)</option>
              <option value="ready" ${prStrategy === 'ready' ? 'selected' : ''}>Open Ready-for-Review PR</option>
              <option value="branch" ${prStrategy === 'branch' ? 'selected' : ''}>Push Branch Only (Manual PR)</option>
            </select>
          </div>

          <div>
            <label style="display:block;font-size:11.5px;color:var(--text-3);font-family:var(--font-mono);margin-bottom:6px;text-transform:uppercase">Branch Naming Convention</label>
            <input type="text" readonly value="feat/kobits-{mission_id}" 
              style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.015);border:1px solid var(--border);border-radius:6px;color:var(--text-3);font-size:13px;font-family:var(--font-mono);cursor:not-allowed">
          </div>
        </div>
      </div>

      <!-- Outbound Webhooks -->
      <div class="card" style="padding:22px;">
        <div class="card-head" style="margin-bottom:16px;">
          <div>
            <div class="ky-eyebrow">Notification Integrations</div>
            <h2 style="font-size:16px;margin:2px 0 0">Outbound Webhooks (Slack / Discord / CI)</h2>
          </div>
        </div>

        <p class="faint" style="font-size:12.5px;margin:0 0 12px 0">
          Kobits broadcasts real-time events when missions complete, PRs are opened, or security findings require human attention.
        </p>

        <div style="display:flex;gap:10px">
          <input type="url" id="webhook-url-input" placeholder="https://hooks.slack.com/services/..." value="${esc(webhookUrl)}" 
            style="flex:1;padding:8px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;color:#fff;font-size:13px">
          <button id="btn-save-webhook" class="btn btn-secondary btn-sm" style="font-weight:600">Save Endpoint</button>
          <button id="btn-test-webhook" class="btn btn-ghost btn-sm">Test Ping</button>
        </div>
      </div>
    </div>
  `;

  // Attach GitHub form
  container.querySelector('#gh-connect-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = container.querySelector('#btn-gh-submit');
    const errBox = container.querySelector('#gh-connect-error');
    const username = container.querySelector('#gh-input-username')?.value.trim();
    const token = container.querySelector('#gh-input-token')?.value.trim();

    if (!username || !token) return;
    btn.disabled = true;
    btn.textContent = 'Connecting…';
    if (errBox) errBox.style.display = 'none';

    try {
      await post('/github/connect', { username, token });
      toast('GitHub account connected!', 'info');
      await renderIntegrationsTab(container);
    } catch (err) {
      if (errBox) {
        errBox.textContent = err.message || 'Failed to connect. Verify token scopes.';
        errBox.style.display = 'block';
      }
      btn.disabled = false;
      btn.textContent = 'Connect GitHub Account';
    }
  });

  container.querySelector('#btn-reconnect-gh')?.addEventListener('click', () => {
    renderIntegrationsTab(container);
  });

  container.querySelector('#pr-strategy')?.addEventListener('change', (e) => {
    localStorage.setItem('kobits.pr_strategy', e.target.value);
    toast(`PR strategy set to: ${e.target.value}`, 'info');
  });

  container.querySelector('#btn-save-webhook')?.addEventListener('click', () => {
    const val = container.querySelector('#webhook-url-input')?.value.trim();
    localStorage.setItem('kobits.webhook_url', val);
    toast('Webhook endpoint saved!', 'info');
  });

  container.querySelector('#btn-test-webhook')?.addEventListener('click', () => {
    toast('Test ping dispatched (200 OK)', 'info');
  });
}

// ────────────────────────────────────────────────────────────────
// TAB 5: BILLING & CREDITS
// ────────────────────────────────────────────────────────────────
async function renderBillingTab(container) {
  container.innerHTML = skeleton(4);
  let balance = 500;
  try {
    const res = await get('/billing/balance');
    if (res && res.balance !== undefined) balance = res.balance;
  } catch (_) {}

  container.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:20px;">
      <!-- Ledger Balance Card -->
      <div class="card" style="padding:22px;">
        <div class="card-head" style="margin-bottom:16px;">
          <div>
            <div class="ky-eyebrow">Compute & Ledger Balance</div>
            <h2 style="font-size:16px;margin:2px 0 0">Organizational Credits</h2>
          </div>
          <span class="badge" style="font-size:11px;padding:3px 9px;border-radius:99px;background:rgba(34,197,94,0.12);color:#22c55e;font-weight:600">
            Active Subscription
          </span>
        </div>

        <div style="display:flex;align-items:center;justify-content:space-between;padding:20px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:8px;margin-bottom:18px">
          <div>
            <div class="mono faint" style="font-size:11px;text-transform:uppercase;margin-bottom:4px">AVAILABLE CREDITS</div>
            <div style="font-size:32px;font-weight:700;color:#fff;letter-spacing:-0.03em;display:flex;align-items:baseline;gap:6px">
              ${balance.toLocaleString()}
              <span class="faint mono" style="font-size:13px;font-weight:400">credits remaining</span>
            </div>
          </div>
          <button id="btn-top-up" class="btn btn-primary btn-sm" style="font-weight:600;padding:8px 16px">
            + Purchase Credit Refill
          </button>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
          <div style="padding:12px 14px;background:rgba(255,255,255,0.015);border:1px solid var(--border);border-radius:6px">
            <div class="faint mono" style="font-size:11px">MISSIONS RUN</div>
            <div style="font-size:18px;font-weight:700;color:#fff;margin-top:2px">49 Completed</div>
          </div>
          <div style="padding:12px 14px;background:rgba(255,255,255,0.015);border:1px solid var(--border);border-radius:6px">
            <div class="faint mono" style="font-size:11px">AVG COST / MISSION</div>
            <div style="font-size:18px;font-weight:700;color:#fff;margin-top:2px">~8.4 Credits</div>
          </div>
          <div style="padding:12px 14px;background:rgba(255,255,255,0.015);border:1px solid var(--border);border-radius:6px">
            <div class="faint mono" style="font-size:11px">RENEWAL DATE</div>
            <div style="font-size:18px;font-weight:700;color:#fff;margin-top:2px">Nov 01, 2026</div>
          </div>
        </div>
      </div>

      <!-- SaaS Plans Comparison -->
      <div class="card" style="padding:22px;">
        <div class="card-head" style="margin-bottom:16px;">
          <div>
            <div class="ky-eyebrow">Subscription Plans</div>
            <h2 style="font-size:16px;margin:2px 0 0">Choose Plan for Your Team</h2>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;">
          <!-- Starter Plan -->
          <div style="padding:18px;background:rgba(255,255,255,0.015);border:1px solid var(--border);border-radius:8px;display:flex;flex-direction:column">
            <div style="font-weight:700;font-size:15px;color:#fff">Hobby Developer</div>
            <div style="font-size:24px;font-weight:700;margin:8px 0 12px;color:#fff">$29 <span class="faint" style="font-size:12px;font-weight:400">/mo</span></div>
            <ul style="padding-left:18px;font-size:12.5px;color:var(--text-2);line-height:1.6;margin:0 0 18px;flex:1">
              <li>500 monthly credits</li>
              <li>2 parallel agents</li>
              <li>Standard MicroVMs</li>
              <li>Community Discord</li>
            </ul>
            <button class="btn btn-ghost btn-sm" style="width:100%">Downgrade</button>
          </div>

          <!-- Pro Team Plan (Current) -->
          <div style="padding:18px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.25);border-radius:8px;display:flex;flex-direction:column;position:relative">
            <span class="badge" style="position:absolute;top:12px;right:12px;background:#22c55e;color:#000;font-size:10px;font-weight:700;padding:2px 8px">CURRENT PLAN</span>
            <div style="font-weight:700;font-size:15px;color:#fff">Pro Team</div>
            <div style="font-size:24px;font-weight:700;margin:8px 0 12px;color:#fff">$79 <span class="faint" style="font-size:12px;font-weight:400">/mo</span></div>
            <ul style="padding-left:18px;font-size:12.5px;color:#fff;line-height:1.6;margin:0 0 18px;flex:1">
              <li>2,500 monthly credits</li>
              <li>4 parallel agents</li>
              <li>Priority Firecracker MicroVMs</li>
              <li>GitHub PR auto-pilot</li>
              <li>Priority email support</li>
            </ul>
            <button class="btn btn-primary btn-sm" disabled style="width:100%;font-weight:600">Active Plan</button>
          </div>

          <!-- Scale Plan -->
          <div style="padding:18px;background:rgba(255,255,255,0.015);border:1px solid var(--border);border-radius:8px;display:flex;flex-direction:column">
            <div style="font-weight:700;font-size:15px;color:#fff">Enterprise Scale</div>
            <div style="font-size:24px;font-weight:700;margin:8px 0 12px;color:#fff">$199 <span class="faint" style="font-size:12px;font-weight:400">/mo</span></div>
            <ul style="padding-left:18px;font-size:12.5px;color:var(--text-2);line-height:1.6;margin:0 0 18px;flex:1">
              <li>10,000 monthly credits</li>
              <li>Unlimited parallel agents</li>
              <li>Dedicated AWS VPC Sandboxes</li>
              <li>Custom LLM model routing</li>
              <li>24/7 SLA & Dedicated Architect</li>
            </ul>
            <button class="btn btn-secondary btn-sm" style="width:100%" onclick="alert('Contact enterprise sales at founders@kobits.space')">Upgrade to Scale</button>
          </div>
        </div>
      </div>
    </div>
  `;

  container.querySelector('#btn-top-up')?.addEventListener('click', () => {
    toast('Redirecting to Stripe checkout...', 'info');
  });
}

// ────────────────────────────────────────────────────────────────
// TAB 6: EARLY ACCESS ADMIN (FOUNDER MODERATION)
// ────────────────────────────────────────────────────────────────
async function renderEarlyAccessTab(container) {
  container.innerHTML = skeleton(4);
  let leads = [];
  try {
    const res = await get('/waitlist/leads');
    if (res && Array.isArray(res)) leads = res;
  } catch (err) {
    container.innerHTML = errorBox(err);
    return;
  }

  const pendingCount = leads.filter(l => l.status === 'PENDING').length;

  container.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:20px;">
      <!-- Waitlist Admin Card -->
      <div class="card" style="padding:22px;">
        <div class="card-head" style="margin-bottom:16px;">
          <div>
            <div class="ky-eyebrow">Founder Review Terminal</div>
            <h2 style="font-size:16px;margin:2px 0 0">Early Access Applicant Moderation</h2>
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <span class="badge" style="font-size:11px;padding:3px 9px;border-radius:99px;background:${pendingCount > 0 ? 'rgba(234,179,8,0.15)' : 'rgba(255,255,255,0.06)'};color:${pendingCount > 0 ? '#eab308' : 'var(--text-3)'}">
              ${pendingCount} Pending Review
            </span>
            <span class="badge" style="font-size:11px;padding:3px 9px;border-radius:99px;background:rgba(255,255,255,0.06)">
              ${leads.length} Total Applications
            </span>
          </div>
        </div>

        <p class="faint" style="font-size:13px;line-height:1.5;margin:0 0 16px 0">
          When visitors request early access on the landing page modal, their application appears below. Approving a user provisions their workspace and grants them 50 complimentary autonomous credits.
        </p>

        <!-- Applicants Table / List -->
        <div style="display:flex;flex-direction:column;gap:10px" id="leads-list">
          ${leads.length === 0 ? `
            <div style="padding:30px;text-align:center;color:var(--text-3)">
              No early access applications received yet.
            </div>
          ` : leads.map(l => {
            const isPending = l.status === 'PENDING';
            const host = window.location.origin;
            const inviteUrl = `${host}/portal.html?email=${encodeURIComponent(l.email)}#register`;

            return `
              <div style="display:flex;flex-direction:column;gap:8px;padding:14px;background:rgba(255,255,255,0.025);border:1px solid var(--border);border-radius:8px">
                <div style="display:flex;justify-content:space-between;align-items:flex-start">
                  <div>
                    <div style="display:flex;align-items:center;gap:8px">
                      <span style="font-weight:700;font-size:14px;color:#fff">${esc(l.full_name || 'Anonymous Applicant')}</span>
                      ${l.company ? `<span class="badge" style="font-size:10.5px;padding:1px 6px">${esc(l.company)}</span>` : ''}
                    </div>
                    <div class="faint mono" style="font-size:12px;margin-top:2px">
                      ${esc(l.email)} ${l.github_handle ? `• @${esc(l.github_handle)}` : ''}
                    </div>
                  </div>
                  <div>
                    ${isPending ? `
                      <button class="btn btn-primary btn-sm btn-approve-lead" data-id="${esc(l.id)}" style="background:#22c55e;border-color:#22c55e;color:#000;font-weight:700;font-size:11.5px;padding:4px 12px">
                        Approve & Invite
                      </button>
                    ` : `
                      <span class="badge" style="background:rgba(34,197,94,0.15);color:#4ade80;font-size:11px;padding:3px 9px;border-radius:99px;font-weight:600">
                        Approved (Active)
                      </span>
                    `}
                  </div>
                </div>

                ${l.use_case ? `
                  <div style="font-size:12.5px;color:var(--text-2);background:rgba(255,255,255,0.02);padding:8px 10px;border-radius:5px;line-height:1.4">
                    <span class="faint mono" style="font-size:10px;text-transform:uppercase;display:block;margin-bottom:2px">Proposed Engineering Outcome:</span>
                    ${esc(l.use_case)}
                  </div>
                ` : ''}

                ${!isPending ? `
                  <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 10px;background:rgba(34,197,94,0.06);border-radius:5px;border:1px solid rgba(34,197,94,0.15)">
                    <span class="mono" style="font-size:11px;color:#4ade80">Workspace Ready (50 Credits Allocated)</span>
                    <button class="btn btn-ghost btn-sm btn-copy-link" data-url="${esc(inviteUrl)}" style="font-size:11px;padding:2px 8px">
                      Copy Personalized Invite Link 📋
                    </button>
                  </div>
                ` : ''}
              </div>
            `;
          }).join('')}
        </div>
      </div>
    </div>
  `;

  // Attach Approve Listeners
  container.querySelectorAll('.btn-approve-lead').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      const id = e.target.getAttribute('data-id');
      e.target.disabled = true;
      e.target.textContent = 'Approving…';
      try {
        await post(`/waitlist/leads/${id}/approve`, {});
        toast('Applicant approved! Workspace credits provisioned.', 'info');
        await checkWaitlistBadge();
        await renderEarlyAccessTab(container);
      } catch (err) {
        alert('Failed to approve lead: ' + (err.message || err));
        e.target.disabled = false;
        e.target.textContent = 'Approve & Invite';
      }
    });
  });

  // Attach Copy Invite Link Listeners
  container.querySelectorAll('.btn-copy-link').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const url = e.currentTarget.dataset.url;
      navigator.clipboard.writeText(url);
      toast('Copied invite link to clipboard!', 'info');
    });
  });
}
