/**
 * Dashboard 2: Agency Operations & Financial Hub ("The Kyros Operator & Margin Command Center")
 * Route: #admin (or /admin)
 * Target Persona: Agency Founder, Managing Partner, and Lead Engineers (The 5% Human-in-the-Loop).
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

export async function renderAdminConsoleView(container, params = {}) {
    if (!container) return;

    const selectedOrg = params.org || localStorage.getItem('kobits_admin_filter_org') || '';

    container.innerHTML = `
        <div class="p-6 max-w-7xl mx-auto space-y-6 text-zinc-100">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-4">
                <span class="px-2.5 py-1 text-xs font-mono uppercase tracking-wider rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30">
                    Agency Operations & Financial Hub
                </span>
                <span class="text-xs text-zinc-400">Loading P&L telemetry & 5% Operator Approval Gate...</span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-4 gap-4 animate-pulse">
                <div class="h-28 bg-zinc-900 border border-zinc-800 rounded-xl"></div>
                <div class="h-28 bg-zinc-900 border border-zinc-800 rounded-xl"></div>
                <div class="h-28 bg-zinc-900 border border-zinc-800 rounded-xl"></div>
                <div class="h-28 bg-zinc-900 border border-zinc-800 rounded-xl"></div>
            </div>
        </div>
    `;

    let data;
    try {
        const query = selectedOrg ? `?organization_id=${encodeURIComponent(selectedOrg)}` : '';
        data = await apiFetch(`/agency-admin/financials${query}`);
    } catch (err) {
        const tok = localStorage.getItem('kobits_token');
        const errMsg = String((err && err.message) || '').toLowerCase();
        const isUnauth = !tok || errMsg.includes('credential') || errMsg.includes('not authenticated') || errMsg.includes('unauthorized') || Boolean(err && err.status === 401);
        if (isUnauth) {
            window.location.href = '/portal.html?auth_notice=admin_required#login';
            return;
        }
        container.innerHTML = `
            <div class="p-8 max-w-3xl mx-auto">
                <div class="bg-red-950/30 border border-red-500/40 rounded-2xl p-6 text-red-200 space-y-3">
                    <div class="text-xs font-mono uppercase text-red-400">RBAC Security Boundary (403 Forbidden)</div>
                    <h2 class="text-lg font-semibold text-white">Agency Operator Role Required</h2>
                    <p class="text-sm text-red-300/90">${escapeHtml(err.message || 'Only Agency Admins and Lead Engineers can access the Operations & Financial Hub.')}</p>
                    <a href="#portal" class="inline-block px-4 py-2 text-xs font-semibold rounded-xl bg-emerald-500 text-zinc-950">
                        Return to Executive Client Portal (#portal)
                    </a>
                </div>
            </div>
        `;
        return;
    }

    const clients = data.clients || [];
    const switcherClients = data.all_clients || clients;
    const approvalQueue = data.approval_queue || [];
    if (typeof window.syncTriadNavBar === 'function') {
        window.syncTriadNavBar('admin', 'ADMIN');
    }

    container.innerHTML = `
        <div class="p-6 max-w-7xl mx-auto space-y-8 text-zinc-100" id="agency-admin-surface">
            <!-- Header Bar & Multi-Tenant Client Switcher -->
            <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-4 bg-gradient-to-r from-zinc-900 via-zinc-900 to-amber-950/25 border border-zinc-800 rounded-2xl p-6 shadow-xl">
                <div class="space-y-1">
                    <div class="flex items-center gap-2">
                        <span class="px-2.5 py-0.5 text-[11px] font-mono uppercase tracking-wider rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30">
                            Agency Command Center
                        </span>
                        <span class="text-xs font-mono text-zinc-400">95% Autonomous Pod • 5% Lead Engineer Gate</span>
                    </div>
                    <h1 class="text-2xl font-bold text-white">Agency Operations, P&L Margin & Approval Hub</h1>
                    <p class="text-sm text-zinc-400">
                        Monitor multi-tenant gross margins, onboard $4k–$8k/mo retainer clients in 1 click, and govern PR sign-offs before client delivery.
                    </p>
                </div>

                <div class="flex flex-wrap items-center gap-3">
                    <!-- Multi-Tenant Client Switcher -->
                    <div class="flex items-center gap-2 bg-zinc-950 border border-zinc-700 rounded-xl px-3 py-1.5">
                        <span class="text-[11px] font-mono text-zinc-400">Tenant:</span>
                        <select id="admin-tenant-switcher" class="bg-transparent text-xs font-semibold text-white focus:outline-none">
                            <option value="" ${!selectedOrg ? 'selected' : ''} class="bg-zinc-900">All Managed Clients (${switcherClients.length})</option>
                            ${switcherClients.map(c => `
                                <option value="${escapeHtml(c.organization_id)}" ${selectedOrg === c.organization_id ? 'selected' : ''} class="bg-zinc-900">
                                    ${escapeHtml(c.client_name)} ($${Number(c.monthly_retainer_usd).toLocaleString()}/mo)
                                </option>
                            `).join('')}
                        </select>
                    </div>

                    <button id="open-onboard-client-btn" class="px-4 py-2.5 text-xs font-semibold rounded-xl bg-amber-400 hover:bg-amber-300 text-zinc-950 shadow-lg shadow-amber-400/20 transition">
                        + Onboard New Client
                    </button>

                    <a href="#console" class="px-3.5 py-2.5 text-xs font-mono rounded-xl bg-zinc-800 hover:bg-zinc-700 text-sky-300 border border-zinc-700 transition">
                        Deep Systems Console (#console) →
                    </a>
                </div>
            </div>

            <!-- 1. Real-Time P&L Margin & Token Cost Tracker -->
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4" id="admin-pnl-cards">
                <div class="bg-zinc-900/90 border border-zinc-800 rounded-2xl p-5">
                    <div class="text-xs font-mono uppercase text-zinc-400">Active Retainer Clients</div>
                    <div class="text-2xl font-bold font-mono text-white mt-1.5" id="metric-active-clients">${Number(data.active_clients_count || clients.length)}</div>
                    <div class="text-xs text-emerald-400 mt-1 font-mono">100% SLA Compliance</div>
                </div>

                <div class="bg-zinc-900/90 border border-zinc-800 rounded-2xl p-5">
                    <div class="text-xs font-mono uppercase text-zinc-400">Monthly Recurring Revenue (MRR)</div>
                    <div class="text-2xl font-bold font-mono text-emerald-400 mt-1.5" id="metric-mrr-usd">$${Number(data.monthly_mrr_usd || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
                    <div class="text-xs text-zinc-400 mt-1">Retainer billing active</div>
                </div>

                <div class="bg-zinc-900/90 border border-zinc-800 rounded-2xl p-5">
                    <div class="text-xs font-mono uppercase text-zinc-400">Total API & MicroVM Cost</div>
                    <div class="text-2xl font-bold font-mono text-sky-400 mt-1.5" id="metric-total-cost-usd">$${Number(data.total_cost_usd || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
                    <div class="text-[11px] text-zinc-400 mt-1 font-mono">
                        LLM: $${Number(data.total_api_cost_usd || 0).toFixed(2)} • VM: $${Number(data.microvm_cost_usd || 0).toFixed(2)} (${Number(data.total_tokens || 0).toLocaleString()} tok)
                    </div>
                </div>

                <div class="bg-zinc-900/90 border border-emerald-500/30 rounded-2xl p-5">
                    <div class="text-xs font-mono uppercase text-emerald-400">Net Gross Margin</div>
                    <div class="flex items-baseline gap-2 mt-1.5">
                        <span class="text-2xl font-bold font-mono text-white" id="metric-net-margin-pct">${Number(data.net_gross_margin_pct || 0).toFixed(1)}%</span>
                        <span class="text-xs font-mono text-emerald-400">($${Number(data.net_gross_margin_usd || 0).toLocaleString()})</span>
                    </div>
                    <div class="text-xs text-zinc-400 mt-1">Target Agency Benchmark: >85%</div>
                </div>
            </div>

            <!-- 2. The 5% Operator Approval Gate Queue -->
            <div class="bg-zinc-900/90 border border-amber-500/30 rounded-2xl p-6 space-y-5">
                <div class="flex flex-col md:flex-row md:items-center justify-between gap-2 border-b border-zinc-800 pb-4">
                    <div>
                        <div class="flex items-center gap-2">
                            <span class="w-2.5 h-2.5 rounded-full bg-amber-400 animate-ping"></span>
                            <span class="text-xs font-mono uppercase tracking-wider text-amber-300">The 5% Operator Approval Gate</span>
                        </div>
                        <h2 class="text-lg font-semibold text-white mt-0.5">Pending Lead Engineer Sign-Off Queue (${approvalQueue.length})</h2>
                        <p class="text-xs text-zinc-400">
                            No autonomous PR ships to the client's GitHub or Executive Portal without your explicit sign-off below.
                        </p>
                    </div>
                </div>

                <div class="space-y-4" id="operator-approval-queue-list">
                    ${approvalQueue.length ? approvalQueue.map((item) => `
                        <div class="bg-zinc-950/90 border border-zinc-800 rounded-xl p-5 space-y-4" data-mission-card="${escapeHtml(item.mission_id)}">
                            <div class="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
                                <div class="space-y-1.5">
                                    <div class="flex flex-wrap items-center gap-2">
                                        <span class="px-2 py-0.5 text-[11px] font-mono rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">
                                            ${escapeHtml(item.status)} • ${escapeHtml(item.client_name)}
                                        </span>
                                        <span class="text-xs font-mono text-zinc-400">
                                            branch: ${escapeHtml(item.branch)} (${escapeHtml(item.commit_sha)})
                                        </span>
                                        <span class="text-[11px] font-mono text-emerald-400">
                                            3/3 Consensus Seals Ready (${escapeHtml(item.consensus_seal_hash)})
                                        </span>
                                    </div>
                                    <h3 class="text-base font-semibold text-white">${escapeHtml(item.title)}</h3>
                                    <p class="text-xs text-zinc-300 leading-relaxed">${escapeHtml(item.description)}</p>
                                </div>

                                <!-- Operator Decision Buttons -->
                                <div class="flex flex-wrap items-center gap-2 shrink-0">
                                    <button
                                        class="gate-approve-btn px-3.5 py-2 text-xs font-semibold rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 shadow-md shadow-emerald-500/15 transition"
                                        data-mission-id="${escapeHtml(item.mission_id)}"
                                    >
                                        ✓ Approve & Ship to Client GitHub
                                    </button>
                                    <button
                                        class="gate-tweak-btn px-3 py-2 text-xs font-semibold rounded-xl bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 border border-amber-500/40 transition"
                                        data-mission-id="${escapeHtml(item.mission_id)}"
                                    >
                                        ↻ Request Agent Tweak
                                    </button>
                                    <button
                                        class="gate-reject-btn px-3 py-2 text-xs font-semibold rounded-xl bg-red-500/15 hover:bg-red-500/25 text-red-300 border border-red-500/30 transition"
                                        data-mission-id="${escapeHtml(item.mission_id)}"
                                    >
                                        ✕ Reject
                                    </button>
                                </div>
                            </div>

                            <!-- Inspection Action Bar -->
                            <div class="flex flex-wrap items-center gap-2 pt-3 border-t border-zinc-800/80">
                                <button
                                    class="toggle-diff-drawer-btn px-3 py-1.5 text-xs font-mono rounded-lg bg-zinc-900 hover:bg-zinc-800 text-zinc-200 border border-zinc-700"
                                    data-target="diff-${escapeHtml(item.mission_id)}"
                                >
                                    [View Full Syntax Diff (${(item.files_changed || []).length} files)]
                                </button>
                                <button
                                    class="toggle-logs-drawer-btn px-3 py-1.5 text-xs font-mono rounded-lg bg-zinc-900 hover:bg-zinc-800 text-zinc-200 border border-zinc-700"
                                    data-target="logs-${escapeHtml(item.mission_id)}"
                                >
                                    [Inspect MicroVM Logs]
                                </button>
                                <a
                                    href="${escapeHtml(item.preview_url)}"
                                    target="_blank"
                                    class="px-3 py-1.5 text-xs font-mono rounded-lg bg-zinc-900 hover:bg-zinc-800 text-sky-400 border border-zinc-700"
                                >
                                    [Open Live Preview ↗]
                                </a>
                                <a
                                    href="${escapeHtml(item.console_url)}"
                                    class="px-3 py-1.5 text-xs font-mono rounded-lg bg-zinc-900 hover:bg-zinc-800 text-amber-300 border border-zinc-700"
                                >
                                    [Deep Telemetry Console (#console) →]
                                </a>
                            </div>

                            <!-- Collapsible Syntax Diff Drawer -->
                            <div id="diff-${escapeHtml(item.mission_id)}" class="hidden bg-zinc-900 border border-zinc-800 rounded-lg p-3">
                                <div class="text-[11px] font-mono text-zinc-400 mb-1.5">Modified Files: ${(item.files_changed || []).map(escapeHtml).join(', ')}</div>
                                <pre class="text-xs font-mono text-emerald-300 overflow-x-auto whitespace-pre-wrap">${escapeHtml(item.diff_preview)}</pre>
                            </div>

                            <!-- Collapsible MicroVM Logs Drawer -->
                            <div id="logs-${escapeHtml(item.mission_id)}" class="hidden bg-zinc-900 border border-zinc-800 rounded-lg p-3">
                                <pre class="text-xs font-mono text-sky-300 overflow-x-auto whitespace-pre-wrap">${(item.microvm_logs || []).map(escapeHtml).join('\n')}</pre>
                            </div>
                        </div>
                    `).join('') : `
                        <div class="bg-zinc-950/60 border border-zinc-800 rounded-xl p-6 text-center text-xs text-zinc-400">
                            All autonomous deliverables have been reviewed and shipped! Queue is currently clear.
                        </div>
                    `}
                </div>
            </div>

            <!-- 3. Client Retention, SLA & Unit Economics Matrix -->
            <div class="bg-zinc-900/90 border border-zinc-800 rounded-2xl p-6 space-y-4">
                <div class="flex items-center justify-between">
                    <div>
                        <h2 class="text-base font-semibold text-white">Client Retention, SLA & Unit Economics Matrix</h2>
                        <p class="text-xs text-zinc-400">Per-tenant retainer revenue, LLM + MicroVM compute cost, and gross margin breakdown</p>
                    </div>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse text-xs">
                        <thead>
                            <tr class="border-b border-zinc-800 text-zinc-400 font-mono uppercase text-[11px]">
                                <th class="py-3 px-3">Client Tenant</th>
                                <th class="py-3 px-3">Active Sprint</th>
                                <th class="py-3 px-3">Retainer MRR</th>
                                <th class="py-3 px-3">API + VM Cost</th>
                                <th class="py-3 px-3">Net Margin</th>
                                <th class="py-3 px-3">SLA Status</th>
                                <th class="py-3 px-3 text-right">Surface</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-zinc-800/70">
                            ${clients.map(c => `
                                <tr class="hover:bg-zinc-800/40">
                                    <td class="py-3.5 px-3 font-semibold text-white">
                                        ${escapeHtml(c.client_name)}
                                        <div class="text-[11px] font-mono text-zinc-400">${escapeHtml(c.client_admin_email)}</div>
                                    </td>
                                    <td class="py-3.5 px-3 text-zinc-300 max-w-xs truncate">${escapeHtml(c.active_sprint)}</td>
                                    <td class="py-3.5 px-3 font-mono text-emerald-400 font-semibold">$${Number(c.monthly_retainer_usd).toLocaleString()}</td>
                                    <td class="py-3.5 px-3 font-mono text-sky-400">$${Number(c.total_cost_usd).toFixed(2)}</td>
                                    <td class="py-3.5 px-3 font-mono">
                                        <span class="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                                            ${Number(c.net_margin_pct).toFixed(1)}% ($${Number(c.net_margin_usd).toLocaleString()})
                                        </span>
                                    </td>
                                    <td class="py-3.5 px-3 font-mono text-zinc-300">${escapeHtml(c.sla_status)}</td>
                                    <td class="py-3.5 px-3 text-right">
                                        <button
                                            class="jump-client-portal-btn px-2.5 py-1 text-[11px] font-mono rounded-lg bg-zinc-800 hover:bg-zinc-700 text-emerald-300 border border-zinc-700"
                                            data-org-id="${escapeHtml(c.organization_id)}"
                                        >
                                            Client Portal ↗
                                        </button>
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- 4. 1-Click Client Onboarding Generator Modal -->
            <div id="onboard-client-modal" class="hidden fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4">
                <div class="bg-zinc-900 border border-zinc-700 rounded-2xl max-w-lg w-full p-6 space-y-4 shadow-2xl">
                    <div class="flex items-center justify-between border-b border-zinc-800 pb-3">
                        <div>
                            <div class="text-xs font-mono text-amber-300">1-Click Tenant Provisioning</div>
                            <h3 class="text-base font-semibold text-white">Onboard New Agency Retainer Client</h3>
                        </div>
                        <button id="close-onboard-modal-btn" class="text-zinc-400 hover:text-white text-sm">✕</button>
                    </div>
                    <form id="onboard-client-form" class="space-y-3.5">
                        <div>
                            <label class="block text-xs font-medium text-zinc-300 mb-1">Client Company Name</label>
                            <input type="text" id="onboard-name" required placeholder="e.g., Veloce Payments Inc." class="w-full px-3.5 py-2 text-sm bg-zinc-950 border border-zinc-700 rounded-xl text-white" />
                        </div>
                        <div class="grid grid-cols-2 gap-3">
                            <div>
                                <label class="block text-xs font-medium text-zinc-300 mb-1">Monthly Retainer ($ USD)</label>
                                <input type="number" id="onboard-retainer" required value="6500" min="500" step="100" class="w-full px-3.5 py-2 text-sm bg-zinc-950 border border-zinc-700 rounded-xl text-white font-mono" />
                            </div>
                            <div>
                                <label class="block text-xs font-medium text-zinc-300 mb-1">Client Executive Email</label>
                                <input type="email" id="onboard-email" required placeholder="founder@veloce.io" class="w-full px-3.5 py-2 text-sm bg-zinc-950 border border-zinc-700 rounded-xl text-white" />
                            </div>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-zinc-300 mb-1">GitHub Repository URL</label>
                            <input type="text" id="onboard-repo" required placeholder="https://github.com/veloce-io/core-api" class="w-full px-3.5 py-2 text-sm bg-zinc-950 border border-zinc-700 rounded-xl text-white font-mono" />
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-zinc-300 mb-1">Initial Sprint Goal</label>
                            <input type="text" id="onboard-goal" value="Week 1 Codebase Discovery, Architecture Audit & Automated Test Suite" class="w-full px-3.5 py-2 text-sm bg-zinc-950 border border-zinc-700 rounded-xl text-white" />
                        </div>
                        <div id="onboard-feedback" class="hidden text-xs font-mono p-3 rounded-lg"></div>
                        <div class="flex items-center justify-end gap-2 pt-2">
                            <button type="button" id="cancel-onboard-btn" class="px-4 py-2 text-xs rounded-xl bg-zinc-800 text-zinc-300">Cancel</button>
                            <button type="submit" id="submit-onboard-btn" class="px-4 py-2 text-xs font-semibold rounded-xl bg-amber-400 hover:bg-amber-300 text-zinc-950">
                                Provision Tenant & Run Week 1 Audit
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    `;

    // Wire Tenant Switcher
    const switcher = container.querySelector('#admin-tenant-switcher');
    if (switcher) {
        switcher.addEventListener('change', () => {
            const val = switcher.value;
            if (val) {
                localStorage.setItem('kobits_admin_filter_org', val);
                localStorage.setItem('kobits_active_org_id', val);
            } else {
                localStorage.removeItem('kobits_admin_filter_org');
            }
            renderAdminConsoleView(container, { org: val });
        });
    }

    // Wire Jump to Client Portal buttons
    container.querySelectorAll('.jump-client-portal-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const orgId = btn.getAttribute('data-org-id');
            if (orgId) {
                localStorage.setItem('kobits_active_org_id', orgId);
            }
            window.location.hash = '#portal';
        });
    });

    // Wire Diff & Logs Drawers
    container.querySelectorAll('.toggle-diff-drawer-btn, .toggle-logs-drawer-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const targetId = btn.getAttribute('data-target');
            const el = targetId ? document.getElementById(targetId) : null;
            if (el) el.classList.toggle('hidden');
        });
    });

    // Wire 5% Approval Gate Actions: Approve, Request Tweak, Reject
    container.querySelectorAll('.gate-approve-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const missionId = btn.getAttribute('data-mission-id');
            if (!missionId) return;
            btn.disabled = true;
            btn.textContent = 'Shipping PR & Sealing...';
            try {
                await apiFetch('/agency-admin/approve-pr', {
                    method: 'POST',
                    body: JSON.stringify({
                        mission_id: missionId,
                        operator_notes: 'Approved via 5% Operator Approval Gate',
                        push_to_github: true,
                    }),
                });
                renderAdminConsoleView(container, params);
            } catch (err) {
                btn.disabled = false;
                btn.textContent = '✓ Approve & Ship to Client GitHub';
                alert(err.message || 'Approval failed');
            }
        });
    });

    container.querySelectorAll('.gate-tweak-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const missionId = btn.getAttribute('data-mission-id');
            if (!missionId) return;
            const feedback = window.prompt('Enter Lead Engineer tweak instructions for the autonomous pod:', 'Add stricter input validation and edge-case unit test coverage.');
            if (!feedback) return;
            btn.disabled = true;
            try {
                await apiFetch('/agency-admin/request-tweak', {
                    method: 'POST',
                    body: JSON.stringify({
                        mission_id: missionId,
                        feedback,
                        target_agent: 'Forge',
                    }),
                });
                renderAdminConsoleView(container, params);
            } catch (err) {
                btn.disabled = false;
                alert(err.message || 'Failed to dispatch tweak');
            }
        });
    });

    container.querySelectorAll('.gate-reject-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const missionId = btn.getAttribute('data-mission-id');
            if (!missionId) return;
            btn.disabled = true;
            try {
                await apiFetch('/agency-admin/reject-pr', {
                    method: 'POST',
                    body: JSON.stringify({
                        mission_id: missionId,
                        reason: 'Rejected by Lead Engineer at 5% Operator Gate; sandbox rolled back.',
                    }),
                });
                renderAdminConsoleView(container, params);
            } catch (err) {
                btn.disabled = false;
                alert(err.message || 'Failed to reject mission');
            }
        });
    });

    // Wire 1-Click Client Onboarding Modal
    const onboardModal = container.querySelector('#onboard-client-modal');
    const openOnboard = () => { if (onboardModal) onboardModal.classList.remove('hidden'); };
    const closeOnboard = () => { if (onboardModal) onboardModal.classList.add('hidden'); };
    const openBtn = container.querySelector('#open-onboard-client-btn');
    const closeBtn = container.querySelector('#close-onboard-modal-btn');
    const cancelBtn = container.querySelector('#cancel-onboard-btn');
    if (openBtn) openBtn.addEventListener('click', openOnboard);
    if (closeBtn) closeBtn.addEventListener('click', closeOnboard);
    if (cancelBtn) cancelBtn.addEventListener('click', closeOnboard);

    const onboardForm = container.querySelector('#onboard-client-form');
    if (onboardForm) {
        onboardForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const nameEl = container.querySelector('#onboard-name');
            const retainerEl = container.querySelector('#onboard-retainer');
            const emailEl = container.querySelector('#onboard-email');
            const repoEl = container.querySelector('#onboard-repo');
            const goalEl = container.querySelector('#onboard-goal');
            const name = nameEl ? nameEl.value.trim() : '';
            const retainer = Number((retainerEl && retainerEl.value) ? retainerEl.value : 6500);
            const email = emailEl ? emailEl.value.trim() : '';
            const repo = repoEl ? repoEl.value.trim() : '';
            const goal = goalEl ? goalEl.value.trim() : '';
            const feedback = container.querySelector('#onboard-feedback');
            const submitBtn = container.querySelector('#submit-onboard-btn');

            if (!name || !email || !repo) return;
            if (submitBtn) submitBtn.disabled = true;

            try {
                const res = await apiFetch('/agency-admin/onboard', {
                    method: 'POST',
                    body: JSON.stringify({
                        client_name: name,
                        monthly_retainer_usd: retainer,
                        github_repo_url: repo,
                        client_admin_email: email,
                        initial_sprint_goal: goal,
                    }),
                });
                if (feedback) {
                    feedback.className = 'text-xs font-mono p-3 rounded-lg bg-emerald-500/15 text-emerald-300 border border-emerald-500/30';
                    feedback.textContent = `Onboarded ${res.organization.name} ($${retainer}/mo)! Updating P&L...`;
                }
                setTimeout(() => {
                    closeOnboard();
                    renderAdminConsoleView(container, params);
                }, 500);
            } catch (err) {
                if (feedback) {
                    feedback.className = 'text-xs font-mono p-3 rounded-lg bg-red-500/15 text-red-300 border border-red-500/30';
                    feedback.textContent = err.message || 'Onboarding failed';
                }
                if (submitBtn) submitBtn.disabled = false;
            }
        });
    }
}

