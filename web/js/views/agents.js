import { get } from '../api.js';
import {
  emptyState, errorBox, esc, skeleton, timeAgo, compact, titleCase, initials
} from '../ui.js';

const PALETTE = ['#D97757', '#5B8DEF', '#2BA877', '#E5A336', '#9B6DD7', '#E86C8D'];

function getAvatarColor(index) {
  return PALETTE[index % PALETTE.length];
}

function agentCard(agent, index) {
  const color = getAvatarColor(index);
  const inits = initials(agent.name);
  
  let statsHtml = '';
  if (agent.runs === 0) {
    statsHtml = `<div class="faint" style="font-size: 13px; margin-top: 12px;">No runs yet</div>`;
  } else {
    statsHtml = `
      <div class="row" style="margin-top: 12px; font-size: 13px; gap: 12px;">
        <div><strong>${esc(agent.runs)}</strong> <span class="faint">runs</span></div>
        ${agent.success_rate != null ? `<div><strong>${esc(agent.success_rate)}%</strong> <span class="faint">success</span></div>` : ''}
        <div><strong>${compact(agent.tokens)}</strong> <span class="faint">tokens</span></div>
      </div>
      <div class="time" style="margin-top: 8px;">Active: ${timeAgo(agent.last_run_at)}</div>
    `;
  }

  return `
    <div class="card">
      <div class="card-head row" style="align-items: center; gap: 12px;">
        <div style="width: 32px; height: 32px; border-radius: 50%; background-color: ${color}; color: white; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 14px; flex-shrink: 0;">
          ${esc(inits)}
        </div>
        <div style="flex: 1; min-width: 0;">
          <div style="font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${esc(agent.name)}</div>
          <div class="mono faint" style="font-size: 11px;">${esc(titleCase(agent.type))}</div>
        </div>
      </div>
      <div class="card-body">
        <div class="faint" style="font-size: 13px; line-height: 1.4; height: 36px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;" title="${esc(agent.description)}">
          ${esc((agent.description || '').slice(0, 100))}
        </div>
        ${statsHtml}
      </div>
    </div>
  `;
}

export async function render() {
  const view = document.getElementById('view');
  view.innerHTML = `<div class="page fade-in">
    <div style="margin-bottom: 24px;">
      <h1>Engineering Team</h1>
      <p class="faint" id="subtitle">Loading agents...</p>
    </div>
    <div class="grid" id="agents-grid" style="display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));">
      ${skeleton(6)}
    </div>
  </div>`;

  await loadAgents();
}

async function loadAgents() {
  const grid = document.getElementById('agents-grid');
  const subtitle = document.getElementById('subtitle');
  try {
    const list = await get('/dashboard/agents');
    subtitle.textContent = `${list.length} agents total`;
    
    // Sort: runs > 0 first (by runs desc), then 0 runs alphabetically
    list.sort((a, b) => {
      if (a.runs > 0 && b.runs > 0) return b.runs - a.runs;
      if (a.runs > 0) return -1;
      if (b.runs > 0) return 1;
      return a.name.localeCompare(b.name);
    });

    grid.innerHTML = list.length
      ? list.map((a, i) => agentCard(a, i)).join('')
      : emptyState('No agents found', 'No agents are currently registered.');
  } catch (e) {
    grid.style.display = 'block';
    grid.innerHTML = errorBox(e);
    subtitle.textContent = '';
  }
}
