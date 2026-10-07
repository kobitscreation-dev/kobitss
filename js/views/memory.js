import { get } from '../api.js';
import { emptyState, errorBox, esc, skeleton, timeAgo } from '../ui.js';

export async function render() {
  const view = document.getElementById('view');
  view.innerHTML = `<div class="page fade-in">
    <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px;">
      <h1>Memory</h1>
      <label class="chip" title="Project" style="background: var(--bg2); padding: 4px 12px; border-radius: 16px; display: flex; align-items: center; gap: 8px;">
        <select id="project-select" aria-label="Project" style="border: none; background: transparent; outline: none; color: inherit; font-family: inherit; font-size: 14px;">
          <option>Loading…</option>
        </select>
      </label>
    </div>
    
    <div class="tabs" id="category-tabs" style="display: flex; gap: 16px; margin-bottom: 24px; border-bottom: 1px solid var(--border, #eee); padding-bottom: 8px;">
      ${['All', 'Architecture', 'Process', 'Project', 'Agent', 'Decision'].map((tab, i) => 
        `<button class="tab-btn ${i === 0 ? 'active' : ''}" data-cat="${tab.toLowerCase()}" style="background: none; border: none; color: ${i === 0 ? 'inherit' : 'var(--faint)'}; cursor: pointer; font-size: 14px; font-weight: ${i === 0 ? '600' : '400'}; padding: 4px 8px;">${tab}</button>`
      ).join('')}
    </div>

    <div class="list" id="memory-list">
      ${skeleton(6)}
    </div>
  </div>`;

  let currentMemories = [];
  let currentProject = '';
  let currentCategory = 'all';

  const select = document.getElementById('project-select');
  const listEl = document.getElementById('memory-list');
  const tabs = document.querySelectorAll('.tab-btn');

  tabs.forEach(btn => {
    btn.addEventListener('click', (e) => {
      tabs.forEach(t => {
        t.classList.remove('active');
        t.style.color = 'var(--faint)';
        t.style.fontWeight = '400';
      });
      const t = e.currentTarget;
      t.classList.add('active');
      t.style.color = 'inherit';
      t.style.fontWeight = '600';
      currentCategory = t.dataset.cat;
      renderMemories(currentMemories, currentCategory, listEl);
    });
  });

  select.addEventListener('change', async (e) => {
    currentProject = e.target.value;
    await loadMemories(currentProject, listEl, (mem) => {
      currentMemories = mem;
      renderMemories(currentMemories, currentCategory, listEl);
    });
  });

  try {
    const projects = await get('/projects');
    if (projects && projects.length) {
      select.innerHTML = projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
      currentProject = projects[0].id;
      await loadMemories(currentProject, listEl, (mem) => {
        currentMemories = mem;
        renderMemories(currentMemories, currentCategory, listEl);
      });
    } else {
      select.innerHTML = '<option value="">No projects</option>';
      select.disabled = true;
      listEl.innerHTML = emptyState('No memory entries', 'No projects available to load memory from.');
    }
  } catch (e) {
    listEl.innerHTML = errorBox(e);
  }
}

async function loadMemories(projectId, listEl, onSuccess) {
  listEl.innerHTML = skeleton(6);
  try {
    const res = await get(`/memory/${encodeURIComponent(projectId)}/memory`);
    const memories = res.memories || [];
    onSuccess(memories);
  } catch (e) {
    listEl.innerHTML = errorBox(e);
  }
}

function renderMemories(memories, category, listEl) {
  const filtered = category === 'all' 
    ? memories 
    : memories.filter(m => (m.category || '').toLowerCase() === category);

  if (!filtered.length) {
    listEl.innerHTML = emptyState('No memory entries', 'No memory entries for this project yet. Memory is built automatically as Kobits completes missions.');
    return;
  }

  const catColors = {
    architecture: 'accent',
    process: 'warning',
    project: 'success',
    agent: 'info',
    decision: 'error'
  };

  listEl.innerHTML = filtered.map(m => {
    const catClass = catColors[(m.category || '').toLowerCase()] || '';
    const valTrim = (m.value || '').length > 120 ? m.value.substring(0, 120) + '…' : (m.value || '');
    
    return `<div class="list-row" style="align-items: center; gap: 16px;">
      <span class="pill ${catClass}">${esc(m.category)}</span>
      <span class="mono" style="font-weight: 600;">${esc(m.key)}</span>
      <span style="flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--faint);" title="${esc(m.value)}">${esc(valTrim)}</span>
      ${m.confidence != null ? `<span class="pill" style="font-size: 11px;">${esc(m.confidence)}%</span>` : ''}
      <span class="faint hide-sm" style="font-size: 12px;">${esc(m.source)}</span>
      <span class="time">${timeAgo(m.created_at)}</span>
    </div>`;
  }).join('');
}
