import { get, post } from '../api.js';
import { poll, navigate } from '../core.js';
import {
  DASH, duration, esc, errorBox, num, phaseTracker, pill, progressBar,
  RUNNING, skeleton, timeAgo, toast, titleCase, renderDiff
} from '../ui.js';

let missionId;
let currentMission = null;

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

export async function render(id) {
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
  
  const m = await loadMission();
  if (m) {
    loadDiff();
    loadActivity();
    
    if (RUNNING.has(m.status)) {
      const timerId = poll(async () => {
        const updated = await loadMission();
        if (updated) {
          loadDiff();
          loadActivity();
          if (!RUNNING.has(updated.status)) {
            clearInterval(timerId);
          }
        }
      }, 5000);
    }
  }
}
