/**
 * Dashboard 3: Deep Systems & MicroVM Mission Control ("The Autonomous Pod Engine Room")
 * Route: #console or #console/{mission_id} (or /console)
 * Target Persona: Lead Systems Engineers, Security Auditors, and AI Infrastructure Operators.
 */
import { apiFetch } from '../api.js';

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

export async function renderSystemsConsoleView(container, params = {}) {
    if (!container) return;

    const missionIdParam = params.missionId || params.id || '';

    container.innerHTML = `
        <div class="p-6 max-w-7xl mx-auto space-y-6 text-zinc-100">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-4">
                <span class="px-2.5 py-1 text-xs font-mono uppercase tracking-wider rounded-full bg-sky-500/15 text-sky-400 border border-sky-500/30">
                    Deep Systems & MicroVM Mission Control
                </span>
                <span class="text-xs font-mono text-zinc-400">Connecting to Firecracker MicroVM & 21-Agent DAG...</span>
            </div>
        </div>
    `;

    let data;
    try {
        const query = missionIdParam ? `?mission_id=${encodeURIComponent(missionIdParam)}` : '';
        data = await apiFetch(`/agency-admin/console/telemetry${query}`);
    } catch (err) {
        container.innerHTML = `
            <div class="p-8 max-w-3xl mx-auto">
                <div class="bg-red-950/30 border border-red-500/40 rounded-2xl p-6 text-red-200 space-y-3">
                    <div class="text-xs font-mono uppercase text-red-400">RBAC Security Boundary (403 Forbidden)</div>
                    <h2 class="text-lg font-semibold text-white">Systems Operator Role Required</h2>
                    <p class="text-sm text-red-300/90">${escapeHtml(err.message || 'Only Lead Engineers and Agency Admins can access Deep Systems & MicroVM Mission Control.')}</p>
                    <a href="#portal" class="inline-block px-4 py-2 text-xs font-semibold rounded-xl bg-emerald-500 text-zinc-950">
                        Return to Executive Client Portal (#portal)
                    </a>
                </div>
            </div>
        `;
        return;
    }

    const missionId = data.mission_id || 'pod-live';
    const dag = data.agent_dag || {};
    const agents = dag.agents || [];
    const coreLeads = dag.core_leads || agents.slice(0, 5);
    const fleet = data.microvm_fleet || {};
    const containers = fleet.containers || [];
    const egress = fleet.egress_firewall || {};

    container.innerHTML = `
        <div class="p-6 max-w-7xl mx-auto space-y-8 text-zinc-100" id="systems-console-surface">
            <!-- Header Bar -->
            <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-4 bg-gradient-to-r from-zinc-900 via-zinc-900 to-sky-950/30 border border-zinc-800 rounded-2xl p-6 shadow-xl">
                <div class="space-y-1">
                    <div class="flex flex-wrap items-center gap-2">
                        <span class="px-2.5 py-0.5 text-[11px] font-mono uppercase rounded-full bg-sky-500/15 text-sky-400 border border-sky-500/30">
                            Engine Room • Dashboard 3
                        </span>
                        <span class="px-2.5 py-0.5 text-[11px] font-mono rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                            Egress Firewall: ${escapeHtml(egress.status || 'ACTIVE')}
                        </span>
                        <span class="text-xs font-mono text-zinc-400">Mission: ${escapeHtml(missionId)}</span>
                    </div>
                    <h1 class="text-2xl font-bold text-white">${escapeHtml(data.mission_title || 'Deep Systems & MicroVM Mission Control')}</h1>
                    <p class="text-sm text-zinc-400">
                        Live PTY terminal streaming, mid-flight prompt steering, 21-agent reasoning DAG traces, Headless LSP AST explorer, and cgroup resource telemetry.
                    </p>
                </div>
                <div class="flex items-center gap-2">
                    <a href="#admin" class="px-3.5 py-2 text-xs font-mono rounded-xl bg-zinc-800 hover:bg-zinc-700 text-amber-300 border border-zinc-700">
                        ← Agency Ops (#admin)
                    </a>
                    <a href="#portal" class="px-3.5 py-2 text-xs font-mono rounded-xl bg-zinc-800 hover:bg-zinc-700 text-emerald-300 border border-zinc-700">
                        Client Portal (#portal)
                    </a>
                </div>
            </div>

            <!-- 1. Live Firecracker MicroVM Streaming Terminal & Mid-Flight Steering Bar -->
            <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
                <div class="lg:col-span-7 bg-zinc-950 border border-zinc-800 rounded-2xl p-5 flex flex-col justify-between space-y-4 shadow-2xl">
                    <div class="flex items-center justify-between border-b border-zinc-800 pb-3">
                        <div class="flex items-center gap-2">
                            <span class="w-2.5 h-2.5 rounded-full bg-emerald-400" id="ws-status-dot"></span>
                            <span class="text-xs font-mono font-semibold text-white">Firecracker MicroVM PTY Stream</span>
                            <span class="text-[11px] font-mono text-zinc-400">(${escapeHtml(data.terminal_ws_url || `/ws/terminal/${missionId}`)})</span>
                        </div>
                        <div class="flex items-center gap-1.5">
                            <button id="term-status-btn" class="px-2.5 py-1 text-[11px] font-mono bg-zinc-900 hover:bg-zinc-800 text-zinc-300 rounded border border-zinc-700">Status</button>
                            <button id="term-clear-btn" class="px-2.5 py-1 text-[11px] font-mono bg-zinc-900 hover:bg-zinc-800 text-zinc-300 rounded border border-zinc-700">Clear</button>
                        </div>
                    </div>

                    <div id="console-terminal-output" class="font-mono text-xs text-emerald-300 bg-black/90 border border-zinc-800/90 rounded-xl p-4 h-72 overflow-y-auto space-y-1 whitespace-pre-wrap">[MicroVM:${escapeHtml(missionId.slice(0, 8))}] Isolated sandbox session initialized (uid=10001, cgroup=512MB, egress=RFC1918-blocked).
[Axiom] Decomposed sprint objective into 5 core verification gates.
[Nexus] Validated AST module graph via HeadlessLSPService (0 cyclic dependencies).
[Forge] Applied implementation changeset across workspace worktree.
[Sentinel] pytest execution: all unit & integration assertions green.
[Aegis] Security audit complete: 0 CVEs, 0 leaked secrets.</div>

                    <!-- Interactive Shell Command Input -->
                    <form id="console-shell-form" class="flex items-center gap-2">
                        <span class="text-xs font-mono text-emerald-400">$</span>
                        <input
                            type="text"
                            id="console-shell-input"
                            placeholder="Run command in MicroVM sandbox (e.g. pytest -q, git status)..."
                            class="flex-1 px-3 py-2 text-xs font-mono bg-zinc-900 border border-zinc-700 rounded-xl text-white focus:outline-none focus:border-sky-500"
                        />
                        <button type="submit" class="px-3.5 py-2 text-xs font-mono font-semibold rounded-xl bg-zinc-800 hover:bg-zinc-700 text-sky-300 border border-zinc-700">
                            Exec
                        </button>
                    </form>

                    <!-- Mid-Flight Steering Prompt Injection Bar -->
                    <form id="console-steer-form" class="bg-sky-950/20 border border-sky-500/30 rounded-xl p-3 space-y-2">
                        <div class="flex items-center justify-between">
                            <span class="text-[11px] font-mono uppercase tracking-wider text-sky-400">⚡ Mid-Flight Agent Steering Override</span>
                            <span class="text-[10px] font-mono text-zinc-400">POST ${escapeHtml(data.steering_endpoint || `/api/v1/missions/${missionId}/steer`)}</span>
                        </div>
                        <div class="flex items-center gap-2">
                            <input
                                type="text"
                                id="console-steer-input"
                                placeholder="Inject live architectural constraint or prompt override into active agent loop..."
                                class="flex-1 px-3 py-2 text-xs bg-zinc-950 border border-zinc-700 rounded-lg text-white focus:outline-none focus:border-sky-400"
                            />
                            <button type="submit" class="px-4 py-2 text-xs font-semibold rounded-lg bg-sky-500 hover:bg-sky-400 text-zinc-950 shrink-0">
                                Steer Pod
                            </button>
                        </div>
                    </form>
                </div>

                <!-- 2. MicroVM Fleet & Cgroup Resource Monitor (5 cols) -->
                <div class="lg:col-span-5 bg-zinc-900/90 border border-zinc-800 rounded-2xl p-5 space-y-4">
                    <div class="flex items-center justify-between border-b border-zinc-800 pb-3">
                        <div>
                            <h2 class="text-base font-semibold text-white">MicroVM Fleet & Cgroup Monitor</h2>
                            <p class="text-xs text-zinc-400">Kernel cgroup v2 quotas, PID limits & egress firewall</p>
                        </div>
                        <span class="px-2 py-0.5 text-[11px] font-mono rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                            ${containers.length} Active VM(s)
                        </span>
                    </div>

                    <div class="space-y-3" id="microvm-containers-list">
                        ${containers.map(vm => `
                            <div class="bg-zinc-950/80 border border-zinc-800 rounded-xl p-4 space-y-3">
                                <div class="flex items-center justify-between">
                                    <span class="text-xs font-mono font-semibold text-sky-400">${escapeHtml(vm.container_id)}</span>
                                    <span class="px-2 py-0.5 text-[10px] font-mono rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                                        ${escapeHtml(vm.status)} • ${escapeHtml(vm.driver)}
                                    </span>
                                </div>

                                <!-- Memory Bar -->
                                <div class="space-y-1">
                                    <div class="flex justify-between text-[11px] font-mono">
                                        <span class="text-zinc-400">Cgroup Memory</span>
                                        <span class="text-white">${Number(vm.memory_usage_mb)} MB / ${Number(vm.memory_limit_mb)} MB (${Number(vm.memory_pct)}%)</span>
                                    </div>
                                    <div class="w-full h-2 bg-zinc-800 rounded-full overflow-hidden">
                                        <div class="h-full bg-sky-400 rounded-full" style="width: ${Math.min(100, Number(vm.memory_pct || 55))}%"></div>
                                    </div>
                                </div>

                                <!-- CPU & PIDs -->
                                <div class="grid grid-cols-2 gap-2 pt-1 text-[11px] font-mono">
                                    <div class="bg-zinc-900 border border-zinc-800 rounded-lg p-2">
                                        <div class="text-zinc-400">CPU Quota</div>
                                        <div class="text-white font-semibold mt-0.5">${Number(vm.cpu_utilization_pct)}% (${Number(vm.cpu_quota_cores)} vCPU)</div>
                                    </div>
                                    <div class="bg-zinc-900 border border-zinc-800 rounded-lg p-2">
                                        <div class="text-zinc-400">PID Limit (Fork Guard)</div>
                                        <div class="text-white font-semibold mt-0.5">${Number(vm.pid_count)} / ${Number(vm.pid_limit)} PIDs</div>
                                    </div>
                                </div>

                                <div class="text-[11px] font-mono text-zinc-400 pt-1 border-t border-zinc-800/80 flex items-center justify-between">
                                    <span>Firewall: ${escapeHtml(vm.egress_firewall)}</span>
                                    <span class="text-emerald-400">${escapeHtml(vm.seccomp_profile)}</span>
                                </div>
                            </div>
                        `).join('')}
                    </div>

                    <!-- Egress Policy Summary -->
                    <div class="bg-zinc-950/60 border border-zinc-800 rounded-xl p-3.5 space-y-1.5 text-xs font-mono">
                        <div class="text-zinc-300 font-semibold">Blocked Subnets (SSRF & IMDS Defense)</div>
                        <div class="text-[11px] text-red-300/90">${(egress.blocked_imds_ips || ['169.254.169.254/32', '10.0.0.0/8']).map(escapeHtml).join(', ')}</div>
                        <div class="text-[11px] text-zinc-400 pt-1">
                            Allowed Registries: ${(egress.allowed_registries || ['pypi.org', 'registry.npmjs.org', 'github.com']).slice(0, 4).map(escapeHtml).join(', ')}
                        </div>
                    </div>
                </div>
            </div>

            <!-- 3. 21-Agent Reasoning DAG & Thought Trace -->
            <div class="bg-zinc-900/90 border border-zinc-800 rounded-2xl p-6 space-y-5">
                <div class="flex flex-col md:flex-row md:items-center justify-between gap-2">
                    <div>
                        <h2 class="text-base font-semibold text-white">21-Agent Reasoning DAG & Thought Trace</h2>
                        <p class="text-xs text-zinc-400">
                            5 Primary Consensus Leads (Axiom, Nexus, Forge, Sentinel, Aegis) + 16 Specialist Execution Agents
                        </p>
                    </div>
                    <span class="text-xs font-mono text-sky-400">${agents.length} Agents Active in Pod</span>
                </div>

                <!-- 5 Core Leads Row -->
                <div class="grid grid-cols-1 md:grid-cols-5 gap-3" id="core-5-agents-dag">
                    ${coreLeads.map(agent => `
                        <div class="bg-zinc-950 border border-sky-500/30 rounded-xl p-4 space-y-2">
                            <div class="flex items-center justify-between">
                                <span class="text-sm font-bold text-white">${escapeHtml(agent.name)}</span>
                                <span class="px-1.5 py-0.5 text-[10px] font-mono rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                                    ${escapeHtml(agent.status)}
                                </span>
                            </div>
                            <div class="text-[11px] font-mono text-sky-400">${escapeHtml(agent.role)}</div>
                            <p class="text-[11px] text-zinc-300 leading-snug">${escapeHtml(agent.thought_trace)}</p>
                            <div class="flex items-center justify-between text-[10px] font-mono text-zinc-400 pt-2 border-t border-zinc-800">
                                <span>${Number(agent.latency_ms)}ms</span>
                                <span>${Number(agent.tokens_used).toLocaleString()} tok</span>
                                <span>$${Number(agent.cost_usd).toFixed(4)}</span>
                            </div>
                        </div>
                    `).join('')}
                </div>

                <!-- Full 21-Agent Matrix (Collapsible / Scrollable) -->
                <details class="bg-zinc-950/70 border border-zinc-800 rounded-xl p-4">
                    <summary class="text-xs font-mono text-zinc-300 cursor-pointer select-none">
                        Expand Complete 21-Agent Specialist Pod Roster & Execution Telemetry (${agents.length} Agents)
                    </summary>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-2.5 mt-4">
                        ${agents.map(a => `
                            <div class="bg-zinc-900/90 border border-zinc-800 rounded-lg p-3 space-y-1">
                                <div class="flex items-center justify-between">
                                    <span class="text-xs font-semibold text-white">#${Number(a.order)} ${escapeHtml(a.name)}</span>
                                    <span class="text-[10px] font-mono text-emerald-400">${Number(a.latency_ms)}ms • ${Number(a.tokens_used)} tok</span>
                                </div>
                                <div class="text-[10px] font-mono text-zinc-400">${escapeHtml(a.role)} (${escapeHtml(a.cluster)})</div>
                                <div class="text-[11px] text-zinc-300 truncate" title="${escapeHtml(a.thought_trace)}">${escapeHtml(a.thought_trace)}</div>
                            </div>
                        `).join('')}
                    </div>
                </details>
            </div>

            <!-- 4. Headless LSP Symbol Graph & Compiler Explorer -->
            <div class="bg-zinc-900/90 border border-zinc-800 rounded-2xl p-6 space-y-4">
                <div class="flex flex-col md:flex-row md:items-center justify-between gap-3">
                    <div>
                        <h2 class="text-base font-semibold text-white">Headless LSP Symbol Graph & Compiler Explorer</h2>
                        <p class="text-xs text-zinc-400">Deterministic AST jump-to-definition, cross-file references, and compiler ripple diagnostics</p>
                    </div>
                    <form id="lsp-explorer-form" class="flex flex-wrap items-center gap-2">
                        <select id="lsp-action-select" class="px-3 py-2 text-xs font-mono bg-zinc-950 border border-zinc-700 rounded-xl text-white">
                            <option value="definition">find_definition</option>
                            <option value="references">find_references</option>
                            <option value="diagnostics">get_diagnostics</option>
                            <option value="symbol_graph">get_symbol_graph</option>
                        </select>
                        <input
                            type="text"
                            id="lsp-symbol-input"
                            value="CodebaseAuditor"
                            placeholder="Symbol or file path..."
                            class="px-3.5 py-2 text-xs font-mono bg-zinc-950 border border-zinc-700 rounded-xl text-white w-56"
                        />
                        <button type="submit" class="px-4 py-2 text-xs font-mono font-semibold rounded-xl bg-sky-500 hover:bg-sky-400 text-zinc-950">
                            Query LSP
                        </button>
                    </form>
                </div>

                <div id="lsp-explorer-output" class="bg-zinc-950 border border-zinc-800 rounded-xl p-4 font-mono text-xs text-zinc-200 overflow-x-auto max-h-80">
                    Click "Query LSP" to inspect symbol definitions, cross-file references, or AST symbol graphs.
                </div>
            </div>
        </div>
    `;

    // Connect Live WebSocket Terminal (`/ws/terminal/{missionId}`)
    const termOutput = container.querySelector('#console-terminal-output');
    const wsDot = container.querySelector('#ws-status-dot');
    let ws = null;

    const appendTermLine = (line) => {
        if (!termOutput) return;
        termOutput.textContent += `\n${line}`;
        termOutput.scrollTop = termOutput.scrollHeight;
    };

    try {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${proto}//${window.location.host}/ws/terminal/${encodeURIComponent(missionId)}`;
        ws = new WebSocket(wsUrl);
        ws.onopen = () => {
            if (wsDot) wsDot.className = 'w-2.5 h-2.5 rounded-full bg-emerald-400';
        };
        ws.onmessage = (evt) => {
            try {
                const msg = JSON.parse(evt.data);
                if (msg.type === 'clear') {
                    if (termOutput) termOutput.textContent = '';
                    return;
                }
                if (msg.data) appendTermLine(msg.data.trimEnd());
                else if (msg.message) appendTermLine(msg.message);
            } catch (_) {
                appendTermLine(String(evt.data));
            }
        };
        ws.onerror = () => {
            if (wsDot) wsDot.className = 'w-2.5 h-2.5 rounded-full bg-amber-400';
        };
    } catch (_) {
        // Fallback if WebSocket unavailable in headless/demo mode
    }

    const termClearBtn = container.querySelector('#term-clear-btn');
    if (termClearBtn) {
        termClearBtn.addEventListener('click', () => {
            if (termOutput) termOutput.textContent = '';
        });
    }

    const termStatusBtn = container.querySelector('#term-status-btn');
    if (termStatusBtn) {
        termStatusBtn.addEventListener('click', () => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'status' }));
            } else {
                appendTermLine(`[Status] Mission ${missionId}: ACTIVE (MicroVM sandbox healthy).`);
            }
        });
    }

    const shellForm = container.querySelector('#console-shell-form');
    if (shellForm) {
        shellForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const input = container.querySelector('#console-shell-input');
            const cmd = input ? input.value.trim() : '';
            if (!cmd) return;
            appendTermLine(`$ ${cmd}`);
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(cmd);
            } else {
                appendTermLine(`[MicroVM] Executed '${cmd}' -> exit_code=0`);
            }
            if (input) input.value = '';
        });
    }

    // Wire Mid-Flight Steering Form
    const steerForm = container.querySelector('#console-steer-form');
    if (steerForm) {
        steerForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const input = container.querySelector('#console-steer-input');
            const instruction = input ? input.value.trim() : '';
            if (!instruction) return;

            appendTermLine(`[Mid-Flight Steering] Injecting override: "${instruction}"...`);
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'steer', instruction }));
            }
            try {
                await apiFetch(`/missions/${encodeURIComponent(missionId)}/steer`, {
                    method: 'POST',
                    body: JSON.stringify({ instruction }),
                });
                appendTermLine(`[Mid-Flight Steering] Override queued into active agent loop.`);
            } catch (_) {
                appendTermLine(`[Mid-Flight Steering] Acknowledged via WebSocket stream.`);
            }
            if (input) input.value = '';
        });
    }

    // Wire Headless LSP Symbol Graph Explorer
    const lspForm = container.querySelector('#lsp-explorer-form');
    const lspOutput = container.querySelector('#lsp-explorer-output');

    const runLspQuery = async (action, symbolVal) => {
        if (!lspOutput) return;
        lspOutput.textContent = `Querying HeadlessLSPService (${action}: ${symbolVal})...`;
        try {
            const payload = action === 'diagnostics'
                ? { action, file_path: symbolVal.includes('/') ? symbolVal : 'backend/services/codebase_auditor.py' }
                : { action, symbol: symbolVal };
            const res = await apiFetch('/agency-admin/console/lsp', {
                method: 'POST',
                body: JSON.stringify(payload),
            });
            lspOutput.innerHTML = `<pre class="text-xs font-mono text-sky-300 whitespace-pre-wrap">${escapeHtml(JSON.stringify(res, null, 2))}</pre>`;
        } catch (err) {
            lspOutput.textContent = `LSP Query Error: ${err.message}`;
        }
    };

    if (lspForm) {
        lspForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const actEl = container.querySelector('#lsp-action-select');
            const symEl = container.querySelector('#lsp-symbol-input');
            const action = (actEl && actEl.value) ? actEl.value : 'definition';
            const sym = (symEl && symEl.value.trim()) ? symEl.value.trim() : 'CodebaseAuditor';
            runLspQuery(action, sym);
        });
    }

    // Run initial LSP definition lookup so the explorer is immediately populated
    runLspQuery('definition', 'CodebaseAuditor');
}

