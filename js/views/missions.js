import { get, post } from '../api.js';
import {
  esc, errorBox, emptyState, pill, progressBar, RUNNING, timeAgo, truncate, titleCase, skeleton
} from '../ui.js';

let allMissions = [];
let currentFilter = 'All';

function getFilteredMissions() {
  if (currentFilter === 'All') return allMissions;
  if (currentFilter === 'Running') return allMissions.filter(m => RUNNING.has(m.status));
  if (currentFilter === 'Needs attention') return allMissions.filter(m => ['AWAITING_APPROVAL', 'BLOCKED', 'FAILED'].includes(m.status));
  if (currentFilter === 'Completed') return allMissions.filter(m => m.status === 'COMPLETED');
  if (currentFilter === 'Failed') return allMissions.filter(m => m.status === 'FAILED');
  return allMissions;
}

function missionRow(m) {
  return `<a class="list-row mission-row" href="#/missions/${esc(m.id)}">
    <span>${pill(m.status)}</span>
    <span style="min-width:0">
      <div class="title">${esc(truncate(m.title, 60))}</div>
      <div class="sub">${esc(titleCase(m.phase))}</div>
    </span>
    <span class="hide-sm" style="flex:1; max-width: 150px;">${progressBar(m)}</span>
    <span class="hide-sm faint" style="font-size:12.5px">${esc(m.priority ? m.priority.toLowerCase() : '')}</span>
    <span class="time">${timeAgo(m.created_at)}</span>
  </a>`;
}

function renderTabs() {
  const tabs = ['All', 'Running', 'Needs attention', 'Completed', 'Failed'];
  const counts = {
    'All': allMissions.length,
    'Running': allMissions.filter(m => RUNNING.has(m.status)).length,
    'Needs attention': allMissions.filter(m => ['AWAITING_APPROVAL', 'BLOCKED', 'FAILED'].includes(m.status)).length,
    'Completed': allMissions.filter(m => m.status === 'COMPLETED').length,
    'Failed': allMissions.filter(m => m.status === 'FAILED').length
  };
  
  return `<div class="row" style="margin-bottom: 16px; gap: 8px;">
    ${tabs.map(t => `<button class="btn btn-sm ${currentFilter === t ? 'btn-primary' : 'btn-ghost'} tab" data-filter="${t}">${t} (${counts[t]})</button>`).join('')}
  </div>`;
}

function renderList() {
  const filtered = getFilteredMissions();
  if (filtered.length === 0) {
    return `<div class="card"><div class="card-body">${emptyState('No missions yet', 'Start one from the overview page.')}</div></div>`;
  }
  return `<div class="card"><div class="list">${filtered.map(missionRow).join('')}</div></div>`;
}

function wireTabs() {
  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      currentFilter = btn.dataset.filter;
      const host = document.getElementById('missions-content');
      if (host) {
        host.innerHTML = renderTabs() + renderList();
        wireTabs();
      }
    });
  });
}

export async function render() {
  const view = document.getElementById('view');
  view.innerHTML = `<div class="page fade-in">
    <h1 style="margin-bottom: 16px;">Missions</h1>
    <div id="missions-content">
      ${skeleton(6)}
    </div>
  </div>`;
  
  try {
    allMissions = await get('/missions/?limit=50');
    const host = document.getElementById('missions-content');
    if (host) {
      host.innerHTML = renderTabs() + renderList();
      wireTabs();
    }
  } catch (e) {
    document.getElementById('missions-content').innerHTML = errorBox(e);
  }
}
