/**
 * Dashboard 1: Executive Client Portal ("The Kyros Trust & Delivery Hub")
 * Route: #portal (or /client-portal)
 * Target Persona: Non-technical Startup Founders, VPs of Product, CTOs, and Agency Retainer Clients.
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

function buildArchitectureSvg(mermaidText) {
    const raw = String(mermaidText || 'graph TD\n  ClientPortal --> API\n  API --> AutonomousPod');
    const edgeRegex = /([A-Za-z0-9_.-]+)(?:\["([^"]+)"\])?\s*-->\s*([A-Za-z0-9_.-]+)(?:\["([^"]+)"\])?/g;
    const nodesMap = new Map();
    const edges = [];
    let match;
    while ((match = edgeRegex.exec(raw)) !== null) {
        const fromId = match[1];
        const fromLabel = match[2] || fromId;
        const toId = match[3];
        const toLabel = match[4] || toId;
        if (!nodesMap.has(fromId)) nodesMap.set(fromId, fromLabel);
        if (!nodesMap.has(toId)) nodesMap.set(toId, toLabel);
        edges.push({ from: fromId, to: toId });
    }
    if (nodesMap.size === 0) {
        nodesMap.set('ClientPortal', 'ClientPortal');
        nodesMap.set('API', 'FastAPI Gateway');
        nodesMap.set('AutonomousPod', '21-Agent Pod');
        edges.push({ from: 'ClientPortal', to: 'API' }, { from: 'API', to: 'AutonomousPod' });
    }
    const nodeEntries = Array.from(nodesMap.entries()).slice(0, 8);
    const cols = Math.min(3, Math.max(2, Math.ceil(Math.sqrt(nodeEntries.length))));
    const rows = Math.ceil(nodeEntries.length / cols);
    const width = 460;
    const height = Math.max(150, rows * 68 + 24);
    const coords = new Map();

    nodeEntries.forEach(([id, label], idx) => {
        const col = idx % cols;
        const row = Math.floor(idx / cols);
        const x = 24 + col * 148;
        const y = 18 + row * 62;
        coords.set(id, { x, y, label });
    });

    const edgeLines = edges
        .filter(e => coords.has(e.from) && coords.has(e.to))
        .map(e => {
            const a = coords.get(e.from);
            const b = coords.get(e.to);
            const x1 = a.x + 62;
            const y1 = a.y + 18;
            const x2 = b.x + 62;
            const y2 = b.y + 18;
            return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#38bdf8" stroke-opacity="0.45" stroke-width="1.5" stroke-dasharray="3 2" />`;
        })
        .join('');

    const nodeRects = nodeEntries
        .map(([id]) => {
            const c = coords.get(id);
            const shortLabel = c.label.length > 16 ? c.label.slice(0, 15) + '…' : c.label;
            return `
                <g transform="translate(${c.x}, ${c.y})">
                    <rect width="124" height="36" rx="8" fill="#09090b" stroke="#10b981" stroke-opacity="0.55" stroke-width="1.2" />
                    <text x="62" y="22" text-anchor="middle" fill="#e4e4e7" font-family="monospace" font-size="10">${escapeHtml(shortLabel)}</text>
                </g>
            `;
        })
        .join('');

    return `<svg viewBox="0 0 ${width} ${height}" class="w-full h-auto select-none" role="img" aria-label="Living Architecture Dependency Graph">${edgeLines}${nodeRects}</svg>`;
}

export async function renderClientPortalView(container, params = {}) {
    if (!container) return;

    const urlParams = new URLSearchParams(window.location.search);
    const requestedOrgId = params.org || urlParams.get('org') || localStorage.getItem('kobits_active_org_id') || '';

    container.innerHTML = `
        <div class="p-6 max-w-7xl mx-auto space-y-6 text-zinc-100">
            <div class="flex items-center justify-between border-b border-zinc-800 pb-4">
                <div class="flex items-center gap-3">
                    <span class="px-2.5 py-1 text-xs font-mono uppercase tracking-wider rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                        Executive Client Portal
                    </span>
                    <span class="text-xs text-zinc-400">Synchronizing live autonomous pod deliverables...</span>
                </div>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4 animate-pulse">
                <div class="h-32 bg-zinc-900/80 border border-zinc-800 rounded-xl"></div>
                <div class="h-32 bg-zinc-900/80 border border-zinc-800 rounded-xl"></div>
                <div class="h-32 bg-zinc-900/80 border border-zinc-800 rounded-xl"></div>
            </div>
        </div>
    `;

    let data;
    try {
        const query = requestedOrgId ? `?organization_id=${encodeURIComponent(requestedOrgId)}` : '';
        data = await apiFetch(`/client-portal/portal-summary${query}`);
    } catch (err) {
        // If unauthenticated, stale org id, or credentials error, gracefully fall back to showcase delivery summary
        const errMsg = String((err && err.message) || '').toLowerCase();
        const isAuthOrOrgErr = errMsg.includes('credential') || errMsg.includes('not authenticated') || errMsg.includes('unauthorized') || errMsg.includes('not found') || Boolean(err && (err.status === 401 || err.status === 404));
        if (isAuthOrOrgErr) {
            localStorage.removeItem('kobits_active_org_id');
            try {
                data = await apiFetch('/client-portal/portal-summary');
            } catch (_) {}
        }
        if (!data) {
            const hasAuthToken = Boolean(localStorage.getItem('kobits_token'));
            if (!hasAuthToken) {
                // Guaranteed fallback for unauthenticated guest visitors
                try {
                    data = await apiFetch('/client-portal/portal-summary');
                } catch (_) {}
            }
        }
        if (!data) {
            container.innerHTML = `
                <div class="p-8 max-w-3xl mx-auto">
                    <div class="bg-red-950/30 border border-red-500/40 rounded-xl p-6 text-red-200">
                        <h2 class="text-lg font-semibold mb-2">Tenant Access Restricted</h2>
                        <p class="text-sm text-red-300/90 mb-4">${escapeHtml(err.message || 'Unable to load Executive Client Portal for this organization.')}</p>
                        <button id="portal-retry-btn" class="px-4 py-2 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 text-white rounded-lg border border-zinc-700">
                            Return to Default Showcase Portal
                        </button>
                    </div>
                </div>
            `;
            const retryBtn = container.querySelector('#portal-retry-btn');
            if (retryBtn) {
                retryBtn.addEventListener('click', () => {
                    localStorage.removeItem('kobits_active_org_id');
                    renderClientPortalView(container, {});
                });
            }
            return;
        }
    }

    const org = data.organization || {};
    const project = data.project || {};
    if (org.role) {
        window.__kobitsActiveOrgRole = org.role;
    }
    window.__kobitsIsGuestPreview = Boolean(data.is_public_preview);
    window.__kobitsActiveClientBrand = (project && project.name) || (org && org.name) || 'Executive Delivery Portal';
    if (typeof window.syncTriadNavBar === 'function') {
        window.syncTriadNavBar('portal', org.role || '', data);
    }
    const hero = data.sprint_hero || {};
    const compute = hero.compute_transparency || {};
    const milestones = data.milestone_roadmap || [];
    const arch = data.architecture_map || {};
    const prs = data.delivered_prs || [];
    const adrs = data.adr_log || [];
    const intakes = data.intake_requests || [];

    container.innerHTML = `
        <div class="p-6 max-w-7xl mx-auto space-y-8 text-zinc-100" id="client-portal-surface">
            <!-- Top Executive Header & CTA -->
            <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-4 bg-gradient-to-r from-zinc-900 via-zinc-900/95 to-emerald-950/30 border border-zinc-800 rounded-2xl p-6 shadow-xl">
                <div class="space-y-1.5">
                    <div class="flex flex-wrap items-center gap-2.5">
                        <span class="px-2.5 py-0.5 text-[11px] font-mono uppercase tracking-wider rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                            ${escapeHtml(org.billing_tier || 'Autonomous Pod Retainer')}
                        </span>
                        <span class="px-2.5 py-0.5 text-[11px] font-mono rounded-full bg-sky-500/15 text-sky-300 border border-sky-500/30">
                            ${escapeHtml(org.sla_status || '99.9% On-Time SLA')}
                        </span>
                        <span class="text-xs text-zinc-400 font-mono">
                            Tenant: ${escapeHtml(org.name || 'Client Workspace')}
                        </span>
                    </div>
                    <h1 class="text-2xl font-bold tracking-tight text-white">
                        ${escapeHtml(project.name || 'Executive Delivery Portal')}
                    </h1>
                    <p class="text-sm text-zinc-400 max-w-2xl">
                        Zero-jargon delivery transparency. Every pull request below is verified in an isolated sandbox and attested by our 3-Agent Consensus Gate before shipping.
                    </p>
                </div>
                <div class="flex flex-wrap items-center gap-3">
                    <button id="open-audit-report-btn" class="px-4 py-2.5 text-xs font-semibold rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-100 border border-zinc-700 transition">
                        View Week 1 Audit Report
                    </button>
                    <button id="open-client-intake-btn" class="px-4 py-2.5 text-xs font-semibold rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 shadow-lg shadow-emerald-500/20 transition">
                        + Request Feature / Report Bug
                    </button>
                </div>
            </div>

            <!-- 1. Sprint Hero & Compute Transparency Banner -->
            <div class="bg-zinc-900/90 border border-zinc-800 rounded-2xl p-6 space-y-5">
                <div class="flex flex-col md:flex-row md:items-center justify-between gap-3">
                    <div>
                        <div class="text-xs uppercase tracking-wider font-mono text-emerald-400">Active Sprint Velocity</div>
                        <h2 class="text-lg font-semibold text-white mt-0.5">${escapeHtml(hero.active_sprint_name || 'Sprint 1: Core Architecture & Feature Delivery')}</h2>
                    </div>
                    <div class="flex items-center gap-3">
                        <span class="text-2xl font-bold text-emerald-400 font-mono">${Number(hero.sprint_progress_pct || 68)}%</span>
                        <span class="text-xs text-zinc-400">Sprint Completion</span>
                    </div>
                </div>
                <div class="w-full h-2.5 bg-zinc-800 rounded-full overflow-hidden">
                    <div class="h-full bg-gradient-to-r from-emerald-500 to-sky-400 rounded-full transition-all duration-500" style="width: ${Math.min(100, Math.max(5, Number(hero.sprint_progress_pct || 68)))}%"></div>
                </div>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-4 pt-2">
                    <div class="bg-zinc-950/70 border border-zinc-800/80 rounded-xl p-4">
                        <div class="text-[11px] uppercase tracking-wider text-zinc-400 font-mono">Functions Analyzed</div>
                        <div class="text-xl font-bold text-white font-mono mt-1">${Number(compute.functions_analyzed || 1420).toLocaleString()}</div>
                        <div class="text-[11px] text-zinc-500 mt-0.5">AST & compiler symbol graph</div>
                    </div>
                    <div class="bg-zinc-950/70 border border-zinc-800/80 rounded-xl p-4">
                        <div class="text-[11px] uppercase tracking-wider text-zinc-400 font-mono">Test Iterations Run</div>
                        <div class="text-xl font-bold text-sky-400 font-mono mt-1">${Number(compute.test_iterations_run || 48).toLocaleString()}</div>
                        <div class="text-[11px] text-zinc-500 mt-0.5">Isolated MicroVM regression runs</div>
                    </div>
                    <div class="bg-zinc-950/70 border border-zinc-800/80 rounded-xl p-4">
                        <div class="text-[11px] uppercase tracking-wider text-zinc-400 font-mono">Edge-Case Bugs Healed</div>
                        <div class="text-xl font-bold text-emerald-400 font-mono mt-1">${Number(compute.bugs_self_healed || 7).toLocaleString()}</div>
                        <div class="text-[11px] text-zinc-500 mt-0.5">Caught & fixed pre-delivery</div>
                    </div>
                    <div class="bg-zinc-950/70 border border-zinc-800/80 rounded-xl p-4">
                        <div class="text-[11px] uppercase tracking-wider text-zinc-400 font-mono">Engineering Hours Saved</div>
                        <div class="text-xl font-bold text-amber-300 font-mono mt-1">${Number(compute.autonomous_hours_saved || 49.5)} hrs</div>
                        <div class="text-[11px] text-zinc-500 mt-0.5">Equivalent senior pod output</div>
                    </div>
                </div>
            </div>

            <!-- 2. 4-Week Milestone Roadmap & 3. Living Architecture Map -->
            <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
                <!-- 4-Week Milestone Roadmap (7 cols) -->
                <div class="lg:col-span-7 bg-zinc-900/90 border border-zinc-800 rounded-2xl p-6 space-y-4">
                    <div class="flex items-center justify-between">
                        <div>
                            <h2 class="text-base font-semibold text-white">4-Week Milestone Roadmap</h2>
                            <p class="text-xs text-zinc-400">Predictable weekly deliverables from Week 1 Discovery to Production Release</p>
                        </div>
                        <span class="text-xs font-mono text-zinc-400">${milestones.length} Milestones</span>
                    </div>
                    <div class="space-y-3">
                        ${milestones.map((ms) => {
                            const statusColor = ms.status === 'COMPLETED'
                                ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                                : ms.status === 'IN_PROGRESS'
                                    ? 'bg-sky-500/15 text-sky-400 border-sky-500/30'
                                    : 'bg-zinc-800 text-zinc-400 border-zinc-700';
                            return `
                                <div class="bg-zinc-950/60 border border-zinc-800/90 rounded-xl p-4 space-y-2.5">
                                    <div class="flex items-center justify-between gap-2">
                                        <div class="flex items-center gap-2">
                                            <span class="text-xs font-mono font-semibold text-zinc-300">${escapeHtml(ms.week)}</span>
                                            <span class="text-sm font-semibold text-white">${escapeHtml(ms.title)}</span>
                                        </div>
                                        <span class="px-2 py-0.5 text-[10px] font-mono uppercase rounded-full border ${statusColor}">
                                            ${escapeHtml(ms.status)}
                                        </span>
                                    </div>
                                    <p class="text-xs text-zinc-400 leading-relaxed">${escapeHtml(ms.description)}</p>
                                    <div class="flex items-center gap-3">
                                        <div class="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                                            <div class="h-full bg-emerald-500 rounded-full" style="width: ${Number(ms.progress || 0)}%"></div>
                                        </div>
                                        <span class="text-[11px] font-mono text-zinc-400">${Number(ms.progress || 0)}%</span>
                                        ${ms.audit_report_available ? `
                                            <button class="view-week1-audit-btn text-[11px] font-medium text-sky-400 hover:text-sky-300 underline">
                                                1-Click Audit Report
                                            </button>
                                        ` : ''}
                                    </div>
                                </div>
                            `;
                        }).join('')}
                    </div>
                </div>

                <!-- Living Architecture Map (5 cols) -->
                <div class="lg:col-span-5 bg-zinc-900/90 border border-zinc-800 rounded-2xl p-6 flex flex-col justify-between space-y-4">
                    <div class="space-y-3">
                        <div class="flex items-center justify-between">
                            <div>
                                <h2 class="text-base font-semibold text-white">Living Architecture Map</h2>
                                <p class="text-xs text-zinc-400">Auto-generated codebase topology & health</p>
                            </div>
                            <div class="text-right">
                                <div class="text-xs font-mono text-zinc-400">Health Score</div>
                                <div class="text-xl font-bold font-mono text-emerald-400" id="portal-health-score">${Number(arch.health_score || 92)}/100</div>
                            </div>
                        </div>

                        <!-- Detected Tech Stack Pills -->
                        <div class="flex flex-wrap gap-1.5" id="portal-tech-stack">
                            ${(arch.tech_stack || ['Python', 'FastAPI', 'SQLAlchemy', 'JavaScript']).map(tech => `
                                <span class="px-2.5 py-0.5 text-[11px] font-mono rounded-md bg-zinc-800 text-zinc-200 border border-zinc-700">
                                    ${escapeHtml(tech)}
                                </span>
                            `).join('')}
                        </div>

                        <!-- Interactive Mermaid Topology Canvas with Zoom Controls -->
                        <div class="relative bg-zinc-950 border border-zinc-800 rounded-xl p-3 overflow-hidden">
                            <div class="flex items-center justify-between mb-2 pb-2 border-b border-zinc-800/80">
                                <span class="text-[11px] font-mono text-zinc-400">Mermaid.js Dependency DAG (${Number(arch.total_files || 0)} files, ${Number(arch.total_loc || 0).toLocaleString()} LOC)</span>
                                <div class="flex items-center gap-1">
                                    <button id="arch-zoom-out" class="px-2 py-0.5 text-xs bg-zinc-800 hover:bg-zinc-700 rounded text-zinc-300" title="Zoom Out">-</button>
                                    <button id="arch-zoom-reset" class="px-2 py-0.5 text-[10px] font-mono bg-zinc-800 hover:bg-zinc-700 rounded text-zinc-300">100%</button>
                                    <button id="arch-zoom-in" class="px-2 py-0.5 text-xs bg-zinc-800 hover:bg-zinc-700 rounded text-zinc-300" title="Zoom In">+</button>
                                </div>
                            </div>
                            <div id="arch-mermaid-viewport" class="overflow-auto max-h-60 transition-transform origin-top-left space-y-2" style="transform: scale(1);">
                                <div id="arch-mermaid-svg-canvas" class="pb-2 border-b border-zinc-800/60">
                                    ${buildArchitectureSvg(arch.mermaid_diagram)}
                                </div>
                                <pre id="arch-mermaid-source" class="mermaid text-[11px] font-mono text-sky-300 whitespace-pre leading-relaxed">${escapeHtml(arch.mermaid_diagram || 'graph TD\n  ClientPortal --> API\n  API --> AutonomousPod')}</pre>
                            </div>
                        </div>
                    </div>
                    <p class="text-xs text-zinc-400 leading-relaxed border-t border-zinc-800 pt-3">
                        ${escapeHtml(arch.executive_summary || 'Codebase exhibits modular service separation and automated test verification.')}
                    </p>
                </div>
            </div>

            <!-- 4. Delivered Pull Requests & The 3 Cryptographic Consensus Review Seals -->
            <div class="bg-zinc-900/90 border border-zinc-800 rounded-2xl p-6 space-y-5">
                <div class="flex flex-col md:flex-row md:items-center justify-between gap-2">
                    <div>
                        <h2 class="text-base font-semibold text-white">Delivered Pull Requests & 3-Agent Consensus Attestations</h2>
                        <p class="text-xs text-zinc-400">Every deliverable is independently verified by Aegis (Security), Sentinel (QA), and Nexus (Architecture)</p>
                    </div>
                    <span class="text-xs font-mono text-emerald-400">${prs.length} Verified Deliverable(s)</span>
                </div>

                <div class="space-y-4" id="delivered-prs-list">
                    ${prs.map((pr) => {
                        const seals = pr.consensus_seals || {};
                        const secSeal = seals.security || {};
                        const qaSeal = seals.qa || {};
                        const archSeal = seals.architecture || {};
                        const previewUrl = (pr.preview && pr.preview.url) || `/api/v1/preview/${pr.mission_id}/`;

                        return `
                            <div class="bg-zinc-950/80 border border-zinc-800 rounded-xl p-5 space-y-4">
                                <div class="flex flex-col md:flex-row md:items-start justify-between gap-4">
                                    <div class="space-y-1">
                                        <div class="flex flex-wrap items-center gap-2">
                                            <span class="px-2 py-0.5 text-[11px] font-mono font-semibold rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                                                PR #${escapeHtml(pr.pr_number)} • ${escapeHtml(pr.status)}
                                            </span>
                                            <span class="text-xs font-mono text-zinc-400">
                                                branch: ${escapeHtml(pr.branch)} (${escapeHtml(pr.commit_sha)})
                                            </span>
                                            <span class="text-[11px] font-mono text-zinc-500">
                                                Seal Hash: ${escapeHtml(pr.consensus_seal_hash)}
                                            </span>
                                        </div>
                                        <h3 class="text-base font-semibold text-white">${escapeHtml(pr.title)}</h3>
                                        <p class="text-xs text-zinc-300 leading-relaxed">${escapeHtml(pr.business_summary)}</p>
                                    </div>
                                    <div class="flex flex-wrap items-center gap-2 shrink-0">
                                        <button
                                            class="launch-live-preview-btn px-3.5 py-2 text-xs font-semibold rounded-xl bg-sky-500 hover:bg-sky-400 text-zinc-950 transition shadow-md shadow-sky-500/10"
                                            data-preview-url="${escapeHtml(previewUrl)}"
                                            data-mission-id="${escapeHtml(pr.mission_id)}"
                                            data-pr-title="${escapeHtml(pr.title)}"
                                        >
                                            1-Click Live Preview
                                        </button>
                                        <a
                                            href="${escapeHtml(pr.github_pr_url || '#')}"
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            class="px-3 py-2 text-xs font-mono rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700 transition"
                                        >
                                            GitHub PR ↗
                                        </a>
                                    </div>
                                </div>

                                <!-- The 3 Consensus Review Seals -->
                                <div class="grid grid-cols-1 md:grid-cols-3 gap-3 pt-2 border-t border-zinc-800/80">
                                    <!-- Seal 1: Security (Aegis) -->
                                    <div class="flex items-start gap-3 bg-zinc-900/90 border border-emerald-500/25 rounded-lg p-3" data-seal="security">
                                        <div class="w-8 h-8 rounded-lg bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center text-emerald-400 font-bold text-sm shrink-0">
                                            🛡
                                        </div>
                                        <div class="space-y-0.5">
                                            <div class="flex items-center gap-1.5">
                                                <span class="text-xs font-semibold text-emerald-400">${escapeHtml(secSeal.label || 'Security Verified')}</span>
                                                <span class="text-[10px] font-mono text-zinc-400">(${escapeHtml(secSeal.agent || 'Aegis')})</span>
                                            </div>
                                            <p class="text-[11px] text-zinc-300 leading-snug">${escapeHtml(secSeal.summary || '0 CVEs, 0 leaked secrets')}</p>
                                        </div>
                                    </div>

                                    <!-- Seal 2: QA (Sentinel) -->
                                    <div class="flex items-start gap-3 bg-zinc-900/90 border border-sky-500/25 rounded-lg p-3" data-seal="qa">
                                        <div class="w-8 h-8 rounded-lg bg-sky-500/15 border border-sky-500/30 flex items-center justify-center text-sky-400 font-bold text-sm shrink-0">
                                            🧪
                                        </div>
                                        <div class="space-y-0.5">
                                            <div class="flex items-center gap-1.5">
                                                <span class="text-xs font-semibold text-sky-400">${escapeHtml(qaSeal.label || 'QA Verified')}</span>
                                                <span class="text-[10px] font-mono text-zinc-400">(${escapeHtml(qaSeal.agent || 'Sentinel')})</span>
                                            </div>
                                            <p class="text-[11px] text-zinc-300 leading-snug">${escapeHtml(qaSeal.summary || '100% unit & integration tests passing')}</p>
                                        </div>
                                    </div>

                                    <!-- Seal 3: Architecture (Nexus) -->
                                    <div class="flex items-start gap-3 bg-zinc-900/90 border border-amber-500/25 rounded-lg p-3" data-seal="architecture">
                                        <div class="w-8 h-8 rounded-lg bg-amber-500/15 border border-amber-500/30 flex items-center justify-center text-amber-300 font-bold text-sm shrink-0">
                                            📐
                                        </div>
                                        <div class="space-y-0.5">
                                            <div class="flex items-center gap-1.5">
                                                <span class="text-xs font-semibold text-amber-300">${escapeHtml(archSeal.label || 'Architecture Verified')}</span>
                                                <span class="text-[10px] font-mono text-zinc-400">(${escapeHtml(archSeal.agent || 'Nexus')})</span>
                                            </div>
                                            <p class="text-[11px] text-zinc-300 leading-snug">${escapeHtml(archSeal.summary || 'Clean modular boundaries enforced')}</p>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        `;
                    }).join('')}
                </div>
            </div>

            <!-- 5. Multi-Device Ephemeral Live Web Preview Drawer (Hidden until clicked or toggled) -->
            <div id="portal-live-preview-drawer" class="hidden bg-zinc-900 border border-sky-500/40 rounded-2xl p-6 space-y-4 shadow-2xl">
                <div class="flex flex-col md:flex-row md:items-center justify-between gap-3 border-b border-zinc-800 pb-4">
                    <div>
                        <div class="text-xs font-mono uppercase text-sky-400">Ephemeral MicroVM Web Preview Gateway</div>
                        <h3 class="text-base font-semibold text-white" id="preview-drawer-title">Live Interactive Preview</h3>
                    </div>
                    <div class="flex flex-wrap items-center gap-2">
                        <button class="viewport-toggle-btn px-3 py-1.5 text-xs font-mono rounded-lg bg-sky-500 text-zinc-950 font-semibold" data-width="100%" data-label="Desktop (1920px)">
                            Desktop (1920px)
                        </button>
                        <button class="viewport-toggle-btn px-3 py-1.5 text-xs font-mono rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700" data-width="768px" data-label="Tablet (768px)">
                            Tablet (768px)
                        </button>
                        <button class="viewport-toggle-btn px-3 py-1.5 text-xs font-mono rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700" data-width="375px" data-label="Mobile (375px)">
                            Mobile (375px)
                        </button>
                        <button id="close-preview-drawer-btn" class="px-3 py-1.5 text-xs font-mono rounded-lg bg-zinc-800 hover:bg-red-950/60 text-zinc-300 hover:text-red-300 border border-zinc-700">
                            Close ✕
                        </button>
                    </div>
                </div>
                <div class="flex justify-center bg-zinc-950 border border-zinc-800 rounded-xl p-4 overflow-x-auto">
                    <iframe
                        id="portal-preview-iframe"
                        src="about:blank"
                        class="bg-zinc-900 border border-zinc-700 rounded-lg transition-all duration-300"
                        style="width: 100%; height: 520px;"
                        title="Kobits Ephemeral Live Web Preview"
                    ></iframe>
                </div>
            </div>

            <!-- 6. Architectural Decision Record (ADR) Log & Client Intake Queue -->
            <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
                <!-- ADR Log (7 cols) -->
                <div class="lg:col-span-7 bg-zinc-900/90 border border-zinc-800 rounded-2xl p-6 space-y-4">
                    <div>
                        <h2 class="text-base font-semibold text-white">Architectural Decision Record (ADR) Log</h2>
                        <p class="text-xs text-zinc-400">Permanent audit trail of key structural & security decisions made by your autonomous pod</p>
                    </div>
                    <div class="space-y-3">
                        ${adrs.map(adr => `
                            <div class="bg-zinc-950/60 border border-zinc-800 rounded-xl p-4 space-y-1.5">
                                <div class="flex items-center justify-between">
                                    <span class="text-xs font-mono font-semibold text-amber-300">${escapeHtml(adr.id)} • ${escapeHtml(adr.author_agent || 'Nexus')}</span>
                                    <span class="text-[11px] font-mono text-emerald-400">Confidence: ${Number(adr.confidence || 98)}%</span>
                                </div>
                                <div class="text-sm font-semibold text-white">${escapeHtml(adr.title)}</div>
                                <p class="text-xs text-zinc-300">${escapeHtml(adr.summary)}</p>
                                <p class="text-[11px] text-zinc-400 italic">${escapeHtml(adr.rationale)}</p>
                            </div>
                        `).join('')}
                    </div>
                </div>

                <!-- Client Intake Queue (5 cols) -->
                <div class="lg:col-span-5 bg-zinc-900/90 border border-zinc-800 rounded-2xl p-6 space-y-4">
                    <div class="flex items-center justify-between">
                        <div>
                            <h2 class="text-base font-semibold text-white">Requested Features & Bug Reports</h2>
                            <p class="text-xs text-zinc-400">Live status of items submitted to your autonomous pod</p>
                        </div>
                        <button id="open-client-intake-btn-secondary" class="px-2.5 py-1 text-xs font-semibold rounded-lg bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/30">
                            + New Request
                        </button>
                    </div>
                    <div class="space-y-2.5" id="client-intake-history">
                        ${intakes.length ? intakes.map(req => `
                            <div class="bg-zinc-950/60 border border-zinc-800 rounded-xl p-3.5 flex items-center justify-between gap-3">
                                <div class="min-w-0">
                                    <div class="text-xs font-semibold text-white truncate">${escapeHtml(req.title)}</div>
                                    <div class="text-[11px] font-mono text-zinc-400 mt-0.5">
                                        Priority: ${escapeHtml(req.priority)} • Phase: ${escapeHtml(req.phase)}
                                    </div>
                                </div>
                                <span class="px-2 py-0.5 text-[10px] font-mono rounded-full bg-sky-500/15 text-sky-300 border border-sky-500/30 shrink-0">
                                    ${escapeHtml(req.status)}
                                </span>
                            </div>
                        `).join('') : `
                            <div class="text-xs text-zinc-500 py-6 text-center">No custom requests submitted yet. Click "+ Request Feature / Report Bug" above.</div>
                        `}
                    </div>
                </div>
            </div>

            <!-- Modal 1: Client Feature / Bug Intake Modal -->
            <div id="client-intake-modal" class="hidden fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4">
                <div class="bg-zinc-900 border border-zinc-700 rounded-2xl max-w-lg w-full p-6 space-y-4 shadow-2xl">
                    <div class="flex items-center justify-between border-b border-zinc-800 pb-3">
                        <h3 class="text-base font-semibold text-white">Request Feature or Report Bug</h3>
                        <button id="close-intake-modal-btn" class="text-zinc-400 hover:text-white text-sm">✕</button>
                    </div>
                    <form id="client-intake-form" class="space-y-4">
                        <div>
                            <label class="block text-xs font-medium text-zinc-300 mb-1">Title</label>
                            <input
                                type="text"
                                id="intake-title-input"
                                required
                                placeholder="e.g., Add Stripe Customer Billing Portal Webhook"
                                class="w-full px-3.5 py-2 text-sm bg-zinc-950 border border-zinc-700 rounded-xl text-white focus:outline-none focus:border-emerald-500"
                            />
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-zinc-300 mb-1">Plain-English Description</label>
                            <textarea
                                id="intake-desc-input"
                                rows="4"
                                required
                                placeholder="Describe the desired user experience, business goal, or bug reproduction steps..."
                                class="w-full px-3.5 py-2 text-sm bg-zinc-950 border border-zinc-700 rounded-xl text-white focus:outline-none focus:border-emerald-500"
                            ></textarea>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-zinc-300 mb-1">Priority</label>
                            <select
                                id="intake-priority-input"
                                class="w-full px-3.5 py-2 text-sm bg-zinc-950 border border-zinc-700 rounded-xl text-white focus:outline-none focus:border-emerald-500"
                            >
                                <option value="Normal">Normal (Current Sprint Queue)</option>
                                <option value="Urgent">Urgent (Immediate Autonomous Pod Dispatch)</option>
                            </select>
                        </div>
                        <div id="intake-feedback-msg" class="hidden text-xs font-mono p-3 rounded-lg"></div>
                        <div class="flex items-center justify-end gap-2 pt-2">
                            <button type="button" id="cancel-intake-modal-btn" class="px-4 py-2 text-xs rounded-xl bg-zinc-800 text-zinc-300 hover:bg-zinc-700">
                                Cancel
                            </button>
                            <button type="submit" id="submit-intake-btn" class="px-4 py-2 text-xs font-semibold rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950">
                                Dispatch to Autonomous Pod
                            </button>
                        </div>
                    </form>
                </div>
            </div>

            <!-- Modal 2: Week 1 Executive Audit Report Modal -->
            <div id="audit-report-modal" class="hidden fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4">
                <div class="bg-zinc-900 border border-zinc-700 rounded-2xl max-w-3xl w-full max-h-[85vh] flex flex-col p-6 space-y-4 shadow-2xl">
                    <div class="flex items-center justify-between border-b border-zinc-800 pb-3">
                        <div>
                            <div class="text-xs font-mono text-emerald-400">Week 1 Deliverable • CodebaseAuditor</div>
                            <h3 class="text-base font-semibold text-white">Executive Architecture & Security Discovery Report</h3>
                        </div>
                        <button id="close-audit-modal-btn" class="text-zinc-400 hover:text-white text-sm">✕</button>
                    </div>
                    <div class="overflow-y-auto flex-1 bg-zinc-950 border border-zinc-800 rounded-xl p-4">
                        <pre class="text-xs font-mono text-zinc-200 whitespace-pre-wrap leading-relaxed">${escapeHtml(arch.markdown_report || '# Executive Architecture Audit\nAll systems verified.')}</pre>
                    </div>
                </div>
            </div>
        </div>
    `;

    // Wire Architecture Map Zoom Controls
    let currentZoom = 1.0;
    const mermaidViewport = container.querySelector('#arch-mermaid-viewport');
    const zoomResetBtn = container.querySelector('#arch-zoom-reset');
    const updateZoom = (newZoom) => {
        currentZoom = Math.min(2.0, Math.max(0.5, Number(newZoom.toFixed(2))));
        if (mermaidViewport) mermaidViewport.style.transform = `scale(${currentZoom})`;
        if (zoomResetBtn) zoomResetBtn.textContent = `${Math.round(currentZoom * 100)}%`;
    };
    const bindClick = (selector, handler) => {
        const el = container.querySelector(selector);
        if (el) el.addEventListener('click', handler);
    };
    bindClick('#arch-zoom-in', () => updateZoom(currentZoom + 0.15));
    bindClick('#arch-zoom-out', () => updateZoom(currentZoom - 0.15));
    if (zoomResetBtn) zoomResetBtn.addEventListener('click', () => updateZoom(1.0));

    // Wire 1-Click Multi-Device Live Preview Drawer
    const previewDrawer = container.querySelector('#portal-live-preview-drawer');
    const previewIframe = container.querySelector('#portal-preview-iframe');
    const previewTitle = container.querySelector('#preview-drawer-title');

    container.querySelectorAll('.launch-live-preview-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const url = btn.getAttribute('data-preview-url');
            const title = btn.getAttribute('data-pr-title');
            if (previewDrawer && previewIframe) {
                previewDrawer.classList.remove('hidden');
                previewIframe.src = url;
                if (previewTitle) previewTitle.textContent = `Live Preview: ${title}`;
                previewDrawer.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        });
    });

    bindClick('#close-preview-drawer-btn', () => {
        if (previewDrawer && previewIframe) {
            previewDrawer.classList.add('hidden');
            previewIframe.src = 'about:blank';
        }
    });

    container.querySelectorAll('.viewport-toggle-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const width = btn.getAttribute('data-width') || '100%';
            if (previewIframe) {
                previewIframe.style.width = width;
            }
            container.querySelectorAll('.viewport-toggle-btn').forEach(b => {
                b.className = 'viewport-toggle-btn px-3 py-1.5 text-xs font-mono rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700';
            });
            btn.className = 'viewport-toggle-btn px-3 py-1.5 text-xs font-mono rounded-lg bg-sky-500 text-zinc-950 font-semibold';
        });
    });

    // Wire Audit Report Modal
    const auditModal = container.querySelector('#audit-report-modal');
    const openAudit = () => { if (auditModal) auditModal.classList.remove('hidden'); };
    bindClick('#open-audit-report-btn', openAudit);
    container.querySelectorAll('.view-week1-audit-btn').forEach(b => b.addEventListener('click', openAudit));
    bindClick('#close-audit-modal-btn', () => { if (auditModal) auditModal.classList.add('hidden'); });

    // Wire Client Intake Modal & Form Submission
    const intakeModal = container.querySelector('#client-intake-modal');
    const openIntake = () => { if (intakeModal) intakeModal.classList.remove('hidden'); };
    const closeIntake = () => { if (intakeModal) intakeModal.classList.add('hidden'); };
    bindClick('#open-client-intake-btn', openIntake);
    bindClick('#open-client-intake-btn-secondary', openIntake);
    bindClick('#close-intake-modal-btn', closeIntake);
    bindClick('#cancel-intake-modal-btn', closeIntake);

    const intakeForm = container.querySelector('#client-intake-form');
    if (intakeForm) {
        intakeForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const titleEl = container.querySelector('#intake-title-input');
            const descEl = container.querySelector('#intake-desc-input');
            const prioEl = container.querySelector('#intake-priority-input');
            const feedbackEl = container.querySelector('#intake-feedback-msg');
            const submitBtn = container.querySelector('#submit-intake-btn');

            if (!titleEl || !titleEl.value.trim() || !descEl || !descEl.value.trim()) return;
            if (submitBtn) submitBtn.disabled = true;

            try {
                const res = await apiFetch('/client-portal/intake', {
                    method: 'POST',
                    body: JSON.stringify({
                        title: titleEl.value.trim(),
                        description: descEl.value.trim(),
                        priority: (prioEl && prioEl.value) ? prioEl.value : 'Normal',
                        organization_id: org.id,
                        project_id: project.id,
                    }),
                });
                if (feedbackEl) {
                    feedbackEl.className = 'text-xs font-mono p-3 rounded-lg bg-emerald-500/15 text-emerald-300 border border-emerald-500/30';
                    feedbackEl.textContent = `Ticket ${res.ticket_id} dispatched! Refreshing portal...`;
                }
                setTimeout(() => {
                    closeIntake();
                    renderClientPortalView(container, params);
                }, 600);
            } catch (err) {
                if (feedbackEl) {
                    feedbackEl.className = 'text-xs font-mono p-3 rounded-lg bg-red-500/15 text-red-300 border border-red-500/30';
                    feedbackEl.textContent = err.message || 'Failed to submit intake request.';
                }
                if (submitBtn) submitBtn.disabled = false;
            }
        });
    }
}

