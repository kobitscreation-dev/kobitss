import { get } from '../api.js';
import { getThemePref, setThemePref } from '../core.js';
import { errorBox, esc, skeleton, titleCase } from '../ui.js';

export async function render() {
  const view = document.getElementById('view');
  view.innerHTML = `<div class="page fade-in">
    <h1 style="margin-bottom: 24px;">Settings</h1>
    
    <div class="stack" style="display: flex; flex-direction: column; gap: 16px; max-width: 600px;">
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
  await loadHealth();
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
