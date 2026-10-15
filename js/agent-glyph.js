// ============================================================================
// KOBITS — ROLE-BASED ANIMATED AGENT INDICATORS (MONOCHROME)
// Inline SVG glyphs keyed by AgentType enum or display name.
// Each returns a compact ~18px SVG with a CSS-driven animation.
// Respects prefers-reduced-motion with a static fallback.
// ============================================================================

/**
 * Normalize an agent type enum, display name, or codename to a role key.
 * @param {string} raw — e.g. 'BACKEND_ENGINEER', 'Forge', 'Backend Engineer'
 * @returns {string} role key for glyph lookup
 */
export function resolveRole(raw) {
  if (!raw) return 'default';
  const s = raw.trim().toUpperCase().replace(/[\s_]+/g, '_');

  // Enum matches
  const ROLE_MAP = {
    ORCHESTRATOR: 'planner',
    SOLUTION_ARCHITECT: 'planner',
    TECHNICAL_LEAD: 'planner',
    PRODUCT_MANAGER: 'planner',
    BUSINESS_ANALYST: 'planner',
    UX_DESIGNER: 'frontend',

    BACKEND_ENGINEER: 'coder',
    FRONTEND_ENGINEER: 'frontend',
    MOBILE_ENGINEER: 'coder',
    AI_ML_ENGINEER: 'coder',
    INTEGRATIONS_ENGINEER: 'coder',
    DATABASE_ENGINEER: 'coder',

    CODE_REVIEWER: 'reviewer',
    QA_ENGINEER: 'qa',
    SECURITY_ENGINEER: 'security',
    DOCUMENTATION_ENGINEER: 'docs',

    DEVOPS_ENGINEER: 'devops',
    SRE: 'devops',
    RELEASE_MANAGER: 'devops',

    PERFORMANCE_ENGINEER: 'perf',
    ACCESSIBILITY_ENGINEER: 'perf',
    DATA_ANALYTICS_ENGINEER: 'perf',
  };

  if (ROLE_MAP[s]) return ROLE_MAP[s];

  // Name / codename matches
  const NAME_MAP = {
    AXIOM: 'planner',
    FORGE: 'coder',
    CORE: 'coder',
    SENTINEL: 'qa',
    AEGIS: 'security',
    MUSE: 'frontend',
    PIXEL: 'frontend',
    ATLAS: 'coder',
    SCHEMA: 'coder',
    REVIEW: 'reviewer',
    PULSE: 'devops',
    SWIFT: 'coder',
    NEURON: 'coder',
    BRIDGE: 'coder',
    VECTOR: 'perf',
  };

  if (NAME_MAP[s]) return NAME_MAP[s];

  // Partial keyword match
  if (s.includes('ORCHESTRAT') || s.includes('ARCHITECT') || s.includes('LEAD') || s.includes('PLAN')) return 'planner';
  if (s.includes('BACKEND') || s.includes('DATABASE') || s.includes('MOBILE') || s.includes('AI_ML') || s.includes('INTEG')) return 'coder';
  if (s.includes('FRONTEND') || s.includes('UX')) return 'frontend';
  if (s.includes('QA') || s.includes('TEST')) return 'qa';
  if (s.includes('SECUR')) return 'security';
  if (s.includes('REVIEW')) return 'reviewer';
  if (s.includes('DOC')) return 'docs';
  if (s.includes('DEVOPS') || s.includes('SRE') || s.includes('RELEASE')) return 'devops';
  if (s.includes('PERF') || s.includes('ACCESS') || s.includes('ANALYT')) return 'perf';

  return 'default';
}

/**
 * Return an inline SVG string for the given agent role.
 * @param {string} typeOrName — AgentType enum, display name, or codename
 * @param {number} [size=18] — pixel size
 * @returns {string} HTML string with inline SVG
 */
export function agentGlyph(typeOrName, size = 18) {
  const role = resolveRole(typeOrName);
  return GLYPHS[role] ? GLYPHS[role](size) : GLYPHS.default(size);
}

// ── SVG Glyph Definitions ──────────────────────────────────────────────────

const GLYPHS = {
  // Planner / Orchestrator — rotating orbit ring
  planner: (sz) => `<span class="ky-glyph ky-glyph-planner" aria-hidden="true" style="width:${sz}px;height:${sz}px">
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="12" cy="12" r="3" fill="currentColor" opacity="0.7"/>
      <ellipse cx="12" cy="12" rx="10" ry="4" stroke="currentColor" stroke-width="1.2" opacity="0.5" class="ky-orbit-ring"/>
      <ellipse cx="12" cy="12" rx="10" ry="4" stroke="currentColor" stroke-width="1.2" opacity="0.3" transform="rotate(60 12 12)" class="ky-orbit-ring"/>
      <ellipse cx="12" cy="12" rx="10" ry="4" stroke="currentColor" stroke-width="1.2" opacity="0.3" transform="rotate(120 12 12)" class="ky-orbit-ring"/>
    </svg>
  </span>`,

  // Backend / Coder — typing cursor nodes
  coder: (sz) => `<span class="ky-glyph ky-glyph-coder" aria-hidden="true" style="width:${sz}px;height:${sz}px">
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <line x1="4" y1="8" x2="11" y2="8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" opacity="0.5"/>
      <line x1="4" y1="12" x2="16" y2="12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" opacity="0.7"/>
      <line x1="4" y1="16" x2="13" y2="16" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" opacity="0.4"/>
      <rect x="17" y="10" width="2" height="6" fill="currentColor" class="ky-cursor-blink"/>
    </svg>
  </span>`,

  // Frontend / UX — layout grid
  frontend: (sz) => `<span class="ky-glyph ky-glyph-frontend" aria-hidden="true" style="width:${sz}px;height:${sz}px">
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" stroke-width="1.2" opacity="0.4"/>
      <line x1="3" y1="9" x2="21" y2="9" stroke="currentColor" stroke-width="1" opacity="0.3"/>
      <line x1="10" y1="9" x2="10" y2="21" stroke="currentColor" stroke-width="1" opacity="0.3"/>
      <rect x="12" y="11" width="7" height="4" rx="1" fill="currentColor" opacity="0.3" class="ky-layout-pulse"/>
    </svg>
  </span>`,

  // Code Reviewer — scanning line over grid
  reviewer: (sz) => `<span class="ky-glyph ky-glyph-reviewer" aria-hidden="true" style="width:${sz}px;height:${sz}px">
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <line x1="4" y1="6" x2="20" y2="6" stroke="currentColor" stroke-width="1" opacity="0.3"/>
      <line x1="4" y1="10" x2="20" y2="10" stroke="currentColor" stroke-width="1" opacity="0.3"/>
      <line x1="4" y1="14" x2="20" y2="14" stroke="currentColor" stroke-width="1" opacity="0.3"/>
      <line x1="4" y1="18" x2="20" y2="18" stroke="currentColor" stroke-width="1" opacity="0.3"/>
      <line x1="2" y1="4" x2="22" y2="4" stroke="currentColor" stroke-width="1.5" opacity="0.8" class="ky-scan-line"/>
    </svg>
  </span>`,

  // QA / Testing — circular sweep dots
  qa: (sz) => `<span class="ky-glyph ky-glyph-qa" aria-hidden="true" style="width:${sz}px;height:${sz}px">
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1" opacity="0.25"/>
      <circle cx="12" cy="3" r="1.5" fill="currentColor" class="ky-sweep-dot" style="--i:0"/>
      <circle cx="20.2" cy="8" r="1.5" fill="currentColor" class="ky-sweep-dot" style="--i:1"/>
      <circle cx="20.2" cy="16" r="1.5" fill="currentColor" class="ky-sweep-dot" style="--i:2"/>
      <circle cx="12" cy="21" r="1.5" fill="currentColor" class="ky-sweep-dot" style="--i:3"/>
      <circle cx="3.8" cy="16" r="1.5" fill="currentColor" class="ky-sweep-dot" style="--i:4"/>
      <circle cx="3.8" cy="8" r="1.5" fill="currentColor" class="ky-sweep-dot" style="--i:5"/>
    </svg>
  </span>`,

  // Security — shield with trace
  security: (sz) => `<span class="ky-glyph ky-glyph-security" aria-hidden="true" style="width:${sz}px;height:${sz}px">
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M12 2L4 6v5c0 5.55 3.84 10.74 8 12 4.16-1.26 8-6.45 8-12V6L12 2z" stroke="currentColor" stroke-width="1.3" fill="none" opacity="0.4"/>
      <path d="M12 2L4 6v5c0 5.55 3.84 10.74 8 12 4.16-1.26 8-6.45 8-12V6L12 2z" stroke="currentColor" stroke-width="1.3" fill="none" class="ky-shield-trace" stroke-dasharray="60" stroke-dashoffset="60"/>
    </svg>
  </span>`,

  // Docs — page with line
  docs: (sz) => `<span class="ky-glyph ky-glyph-docs" aria-hidden="true" style="width:${sz}px;height:${sz}px">
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M6 2h9l5 5v15H6V2z" stroke="currentColor" stroke-width="1.2" opacity="0.4"/>
      <line x1="9" y1="10" x2="17" y2="10" stroke="currentColor" stroke-width="1" opacity="0.3"/>
      <line x1="9" y1="13" x2="15" y2="13" stroke="currentColor" stroke-width="1" opacity="0.3"/>
      <line x1="9" y1="16" x2="16" y2="16" stroke="currentColor" stroke-width="1" opacity="0.3" class="ky-doc-write"/>
    </svg>
  </span>`,

  // DevOps / SRE — connected nodes with flowing line
  devops: (sz) => `<span class="ky-glyph ky-glyph-devops" aria-hidden="true" style="width:${sz}px;height:${sz}px">
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="5" cy="12" r="2.5" stroke="currentColor" stroke-width="1.2" opacity="0.5"/>
      <circle cx="19" cy="6" r="2.5" stroke="currentColor" stroke-width="1.2" opacity="0.5"/>
      <circle cx="19" cy="18" r="2.5" stroke="currentColor" stroke-width="1.2" opacity="0.5"/>
      <line x1="7.5" y1="11" x2="16.5" y2="7" stroke="currentColor" stroke-width="1" opacity="0.3"/>
      <line x1="7.5" y1="13" x2="16.5" y2="17" stroke="currentColor" stroke-width="1" opacity="0.3"/>
      <circle cx="5" cy="12" r="1" fill="currentColor" class="ky-node-pulse"/>
    </svg>
  </span>`,

  // Performance / Analytics — ring gauge
  perf: (sz) => `<span class="ky-glyph ky-glyph-perf" aria-hidden="true" style="width:${sz}px;height:${sz}px">
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1" opacity="0.2"/>
      <path d="M12 3a9 9 0 0 1 7.8 4.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" class="ky-gauge-arc"/>
      <circle cx="12" cy="12" r="2" fill="currentColor" opacity="0.5"/>
    </svg>
  </span>`,

  // Default — simple ring
  default: (sz) => `<span class="ky-glyph ky-glyph-default" aria-hidden="true" style="width:${sz}px;height:${sz}px">
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="12" cy="12" r="8" stroke="currentColor" stroke-width="1.2" opacity="0.4"/>
      <circle cx="12" cy="12" r="3" fill="currentColor" opacity="0.5"/>
    </svg>
  </span>`,
};
