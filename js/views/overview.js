// Overview: greeting, Claude-style composer, stats, recent missions, live pipeline.
import { get, post } from '../api.js';
import { navigate, poll } from '../core.js';
import {
  DASH, duration, emptyState, esc, errorBox, icon, num, phaseStepper, pill, progressBar,
  RUNNING, skeleton, timeAgo, toast, truncate,
} from '../ui.js';

const LAST_PROJECT = 'kobits.lastProject';
const MODE_KEY = 'kobits.autopilot';

function greeting(name) {
  const h = new Date().getHours();
  const part = h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
  const first = (name || '').trim().split(/\s+/)[0];
  return first && !/^(dev|kobits)$/i.test(first) ? `${part}, ${first}` : part;
}

function missionRow(m) {
  return `<a class="list-row mission-row" href="#/missions/${esc(m.id)}">
    <span>${pill(m.status)}</span>
    <span style="min-width:0">
      <div class="title">${esc(m.title)}</div>
      <div class="sub">${esc(m.current_stage || '')}</div>
    </span>
    <span class="mono faint hide-sm">${esc(m.id.slice(0, 8))}</span>
    <span class="hide-sm">${progressBar(m)}</span>
    <span class="hide-sm faint" style="font-size:12.5px">${esc(m.priority ? m.priority.toLowerCase() : '')}</span>
    <span class="time">${timeAgo(m.created_at)}</span>
  </a>`;
}

export async function render() {
  const view = document.getElementById('view');
  view.innerHTML = `<div class="page fade-in">
    <h1 id="greet" style="margin-bottom:24px">${greeting('')}</h1>
    <div class="grid-2">
      <div class="stack">
        <form class="composer" id="composer" autocomplete="off">
          <textarea id="objective" rows="2" placeholder="What should Kobits build next?" aria-label="Mission objective"></textarea>
          <div class="composer-bar">
            <button type="button" class="chip" id="mode-chip" aria-pressed="true">${icon('sparkles', 14)}<span id="mode-label">Autopilot</span></button>
            <label class="chip" title="Project">${icon('folder', 14)}<select id="project" aria-label="Project"><option>Loading…</option></select></label>
            <span class="spacer"></span>
            <button class="send" id="send" type="submit" aria-label="Start mission" disabled>${icon('arrowUp', 18)}</button>
          </div>
        </form>
        <div class="composer-hint" id="mode-hint" style="margin-top:-14px"></div>
        <div class="stats" id="stats">${['Completed', 'Running', 'Needs attention', 'Avg. time'].map((k) =>
          `<div class="card stat"><div class="k">${k}</div><div class="v"><span class="skeleton" style="display:inline-block;width:48px;height:26px"></span></div></div>`).join('')}</div>
        <div class="card">
          <div class="card-head"><h2>Recent missions</h2><a href="#/missions" class="btn btn-ghost btn-sm">View all</a></div>
          <div class="list" id="recent">${skeleton(4)}</div>
        </div>
      </div>
      <div class="card" id="pipeline-card">
        <div class="card-head"><h3>Live pipeline</h3></div>
        <div class="card-body" id="pipeline">${skeleton(6)}</div>
      </div>
    </div>
  </div>`;

  wireComposer();
  await Promise.all([loadSummary(), loadProjectsAndMissions()]);
  poll(() => { loadSummary(); loadProjectsAndMissions(true); }, 6000);
}

function wireComposer() {
  const form = document.getElementById('composer');
  const ta = document.getElementById('objective');
  const send = document.getElementById('send');
  const chip = document.getElementById('mode-chip');

  const setMode = (auto) => {
    localStorage.setItem(MODE_KEY, auto ? '1' : '0');
    chip.classList.toggle('on', auto);
    chip.setAttribute('aria-pressed', String(auto));
    document.getElementById('mode-label').textContent = auto ? 'Autopilot' : 'Ask before executing';
    document.getElementById('mode-hint').textContent = auto
      ? 'Autopilot: Kobits plans, builds and verifies in a sandbox. Your repo only changes when you click "Apply".'
      : 'Kobits will plan first and wait for your approval before writing code.';
  };
  setMode(localStorage.getItem(MODE_KEY) !== '0');
  chip.addEventListener('click', () => setMode(!chip.classList.contains('on')));

  const resize = () => { ta.style.height = 'auto'; ta.style.height = `${Math.min(ta.scrollHeight, 260)}px`; };
  ta.addEventListener('input', () => { send.disabled = !ta.value.trim(); resize(); });
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const objective = ta.value.trim();
    const projectId = document.getElementById('project').value;
    if (!objective) return;
    if (!projectId) { toast('Pick a project first.', 'error'); return; }
    send.disabled = true;
    try {
      const firstLine = objective.split('\n')[0];
      const res = await post(`/missions/projects/${encodeURIComponent(projectId)}/missions`, {
        title: truncate(firstLine, 90),
        objective,
        execution_mode: chip.classList.contains('on') ? 'autopilot' : 'approval_required',
      });
      localStorage.setItem(LAST_PROJECT, projectId);
      toast('Mission started');
      navigate(`/missions/${res.mission_id}`);
    } catch (err) {
      toast(err.message, 'error');
      send.disabled = false;
    }
  });
}

async function loadSummary() {
  try {
    const s = await get('/dashboard/summary');
    document.getElementById('greet').textContent = greeting(s.user?.name);
    const m = s.missions;
    const vals = [
      m ? num(m.completed) : DASH,
      m ? num(m.running) : DASH,
      m ? num(m.needs_attention) : DASH,
      duration(s.avg_duration_seconds),
    ];
    document.querySelectorAll('#stats .v').forEach((el, i) => { el.textContent = vals[i]; });
  } catch (e) {
    document.getElementById('stats').outerHTML = errorBox(e);
  }
}

let projectsLoaded = false;

async function loadProjectsAndMissions(silent = false) {
  const recent = document.getElementById('recent');
  try {
    const [list, projects] = await Promise.all([
      get('/missions/?limit=8'),
      projectsLoaded && silent ? Promise.resolve(null) : get('/projects'),
    ]);

    if (projects) {
      projectsLoaded = true;
      const sel = document.getElementById('project');
      const preferred = localStorage.getItem(LAST_PROJECT) || list[0]?.project_id;
      sel.innerHTML = projects.length
        ? projects.map((p) => `<option value="${esc(p.id)}" ${p.id === preferred ? 'selected' : ''}>${esc(p.name)}</option>`).join('')
        : '<option value="">No projects yet</option>';
    }

    recent.innerHTML = list.length
      ? list.map(missionRow).join('')
      : emptyState('No missions yet', 'Describe what you want built above and Kobits will plan, implement and verify it.');

    const live = list.find((m) => RUNNING.has(m.status)) || list[0];
    const pipe = document.getElementById('pipeline');
    pipe.innerHTML = live
      ? `<a href="#/missions/${esc(live.id)}" style="color:inherit;display:block;margin-bottom:12px">
           <div style="font-weight:500">${esc(truncate(live.title, 60))}</div>
           <div class="row" style="margin-top:6px">${pill(live.status)}<span class="mono faint">${esc(live.id.slice(0, 8))}</span></div>
         </a>${phaseStepper(live)}`
      : '<p class="faint" style="margin:0">Nothing running right now.</p>';
  } catch (e) {
    if (!silent) recent.innerHTML = `<div class="card-body">${errorBox(e)}</div>`;
  }
}
