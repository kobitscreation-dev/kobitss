// Kobits — API Client & Showcase Demo Interceptor
// Delivers pristine enterprise demonstration telemetry to showcase the platform
// without exposing personal/local development database states.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// ── Showcase Fake Enterprise Data ──────────────────────────────────────────

const FAKE_PROJECTS = [
  { id: "demo-proj-core", name: "Cloud Core Infrastructure", description: "Autonomous multi-agent engineering platform runtime", status: "READY" },
  { id: "demo-proj-engine", name: "Distributed ACID WAL Engine", description: "High-throughput write-ahead log & multi-version storage engine", status: "READY" },
  { id: "demo-proj-mesh", name: "Zero-Trust Ingress Gateway", description: "Distributed rate-limiting and telemetry layer", status: "READY" }
];

const FAKE_MILESTONES = [
  {
    mission_id: "demo-msn-wal",
    title: "Distributed Raft Consensus & WAL Storage Engine",
    objective: "Implement a high-throughput write-ahead log (WAL) with deterministic replay, crash recovery semantics, and multi-version concurrency control.",
    status: "COMPLETED",
    phase: "DELIVERY",
    progress: 100,
    branch: "feat/wal-acid-consensus",
    sandbox_dir: "sandboxes/sandbox-8f42",
    file_list: ["storage/wal.py", "storage/crc32.py", "tests/test_wal.py"],
    created_at: new Date(Date.now() - 3600000 * 4).toISOString(),
    completed_at: new Date(Date.now() - 3600000 * 3.2).toISOString(),
    duration_seconds: 2880,
    tasks_total: 4,
    tasks_done: 4,
    pr_count: 1,
    files_changed: 3,
    lines_added: 284,
    lines_removed: 0,
    pr: {
      number: 142,
      url: "https://github.com/kobits/platform/pull/142",
      status: "MERGED",
      checks_status: "PASSED"
    },
    tasks: [
      { id: "t1", title: "Binary frame serialization for append-only log", status: "COMPLETED", phase: "IMPLEMENTATION", agent: "Atlas" },
      { id: "t2", title: "Checksum validation and recovery scanner", status: "COMPLETED", phase: "IMPLEMENTATION", agent: "Forge" },
      { id: "t3", title: "Concurrency race and crash simulation pytest suite", status: "COMPLETED", phase: "VALIDATION", agent: "Sentinel" },
      { id: "t4", title: "Memory leak and thread-safety audit", status: "COMPLETED", phase: "SECURITY", agent: "Aegis" }
    ]
  },
  {
    mission_id: "demo-msn-sandbox",
    title: "Autonomous Pytest Cleanroom Sandbox Orchestration",
    objective: "Provision ephemeral sandbox cleanrooms with isolated venvs and execute real-time AST syntax validation.",
    status: "ACTIVE",
    phase: "VALIDATION",
    progress: 82,
    branch: "feat/cleanroom-sandbox",
    sandbox_dir: "sandboxes/sandbox-cleanroom",
    file_list: ["runtime/sandbox.py", "runtime/venv.py", "tests/test_sandbox.py"],
    created_at: new Date(Date.now() - 3600000 * 1.5).toISOString(),
    completed_at: null,
    duration_seconds: 5400,
    tasks_total: 3,
    tasks_done: 2,
    pr_count: 1,
    files_changed: 3,
    lines_added: 196,
    lines_removed: 12,
    pr: {
      number: 143,
      url: "https://github.com/kobits/platform/pull/143",
      status: "OPEN",
      checks_status: "RUNNING"
    },
    tasks: [
      { id: "t5", title: "Decompose containerized worktree filesystem bindings", status: "COMPLETED", phase: "PLANNING", agent: "Axiom" },
      { id: "t6", title: "Inject environment isolation and dependency boundary checks", status: "COMPLETED", phase: "IMPLEMENTATION", agent: "Forge" },
      { id: "t7", title: "Execute AST syntax and test coverage assertions", status: "IN_PROGRESS", phase: "VALIDATION", agent: "Sentinel" }
    ]
  },
  {
    mission_id: "demo-msn-ratelimit",
    title: "Token Bucket Rate Limiter & Security Ingress Filter",
    objective: "Construct low-latency in-memory rate limiting middleware with sliding window counters and CIDR blacklist guards.",
    status: "COMPLETED",
    phase: "DELIVERY",
    progress: 100,
    branch: "feat/rate-limiter-filter",
    sandbox_dir: "sandboxes/sandbox-mesh",
    file_list: ["gateway/ratelimit.py", "gateway/cidr.py", "tests/test_ratelimit.py"],
    created_at: new Date(Date.now() - 3600000 * 12).toISOString(),
    completed_at: new Date(Date.now() - 3600000 * 10.8).toISOString(),
    duration_seconds: 4320,
    tasks_total: 3,
    tasks_done: 3,
    pr_count: 1,
    files_changed: 3,
    lines_added: 165,
    lines_removed: 8,
    pr: {
      number: 141,
      url: "https://github.com/kobits/platform/pull/141",
      status: "MERGED",
      checks_status: "PASSED"
    },
    tasks: [
      { id: "t8", title: "Implement token bucket synchronization with atomic compare-and-swap", status: "COMPLETED", phase: "IMPLEMENTATION", agent: "Forge" },
      { id: "t9", title: "Validate sliding window clock drift precision", status: "COMPLETED", phase: "VALIDATION", agent: "Sentinel" },
      { id: "t10", title: "Verify OWASP compliance and IP spoofing resilience", status: "COMPLETED", phase: "SECURITY", agent: "Aegis" }
    ]
  },
  {
    mission_id: "demo-msn-lru",
    title: "Optimized LRU Memory Cache with Eviction Telemetry",
    objective: "Build an LRU cache with O(1) read/write lookups, capacity enforcement, and Prometheus metrics export.",
    status: "COMPLETED",
    phase: "DELIVERY",
    progress: 100,
    branch: "feat/lru-cache-telemetry",
    sandbox_dir: "sandboxes/sandbox-lru",
    file_list: ["cache/lru.py", "cache/metrics.py", "tests/test_lru.py"],
    created_at: new Date(Date.now() - 3600000 * 24).toISOString(),
    completed_at: new Date(Date.now() - 3600000 * 23.2).toISOString(),
    duration_seconds: 2880,
    tasks_total: 2,
    tasks_done: 2,
    pr_count: 1,
    files_changed: 3,
    lines_added: 142,
    lines_removed: 4,
    pr: {
      number: 140,
      url: "https://github.com/kobits/platform/pull/140",
      status: "MERGED",
      checks_status: "PASSED"
    },
    tasks: [
      { id: "t11", title: "Doubly-linked list with hash map index", status: "COMPLETED", phase: "IMPLEMENTATION", agent: "Forge" },
      { id: "t12", title: "Capacity limit eviction pytest suite", status: "COMPLETED", phase: "VALIDATION", agent: "Sentinel" }
    ]
  },
  {
    mission_id: "demo-msn-ws",
    title: "Asynchronous WebSocket Real-Time Terminal Gateway",
    objective: "Enable bi-directional human-in-the-loop steering prompts through multiplexed WebSocket connections.",
    status: "COMPLETED",
    phase: "DELIVERY",
    progress: 100,
    branch: "feat/ws-terminal-gateway",
    sandbox_dir: "sandboxes/sandbox-ws",
    file_list: ["services/ws.py", "services/multiplex.py", "tests/test_ws.py"],
    created_at: new Date(Date.now() - 3600000 * 36).toISOString(),
    completed_at: new Date(Date.now() - 3600000 * 35).toISOString(),
    duration_seconds: 3600,
    tasks_total: 2,
    tasks_done: 2,
    pr_count: 1,
    files_changed: 3,
    lines_added: 210,
    lines_removed: 14,
    pr: {
      number: 139,
      url: "https://github.com/kobits/platform/pull/139",
      status: "MERGED",
      checks_status: "PASSED"
    },
    tasks: [
      { id: "t13", title: "Construct connection pool manager and heartbeat monitors", status: "COMPLETED", phase: "IMPLEMENTATION", agent: "Forge" },
      { id: "t14", title: "Stress test disconnect reconnection resilience", status: "COMPLETED", phase: "VALIDATION", agent: "Sentinel" }
    ]
  },
  {
    mission_id: "demo-msn-schema",
    title: "Zero-Downtime Database Schema Migration Verifier",
    objective: "Simulate backward-compatible column migrations and lock avoidance strategies in isolated test worktrees.",
    status: "COMPLETED",
    phase: "DELIVERY",
    progress: 100,
    branch: "feat/migration-verifier",
    sandbox_dir: "sandboxes/sandbox-schema",
    file_list: ["db/migrations.py", "db/lock_check.py", "tests/test_migrations.py"],
    created_at: new Date(Date.now() - 3600000 * 48).toISOString(),
    completed_at: new Date(Date.now() - 3600000 * 46.5).toISOString(),
    duration_seconds: 5400,
    tasks_total: 2,
    tasks_done: 2,
    pr_count: 1,
    files_changed: 3,
    lines_added: 178,
    lines_removed: 22,
    pr: {
      number: 138,
      url: "https://github.com/kobits/platform/pull/138",
      status: "MERGED",
      checks_status: "PASSED"
    },
    tasks: [
      { id: "t15", title: "Analyze table locks and index creation concurrency", status: "COMPLETED", phase: "IMPLEMENTATION", agent: "Atlas" },
      { id: "t16", title: "Verify dual-write backward compatibility invariants", status: "COMPLETED", phase: "VALIDATION", agent: "Sentinel" }
    ]
  }
];

const FAKE_TEAM = [
  { id: "Axiom", name: "Axiom", type: "ORCHESTRATOR", runs: 84, last_run_at: new Date(Date.now() - 3600000 * 1).toISOString(), completed: 84, failed: 0, success_rate: 100, tokens: 105200 },
  { id: "Sentinel", name: "Sentinel", type: "QA_ENGINEER", runs: 76, last_run_at: new Date(Date.now() - 3600000 * 1.5).toISOString(), completed: 76, failed: 0, success_rate: 100, tokens: 94100 },
  { id: "Forge", name: "Forge", type: "BACKEND_ENGINEER", runs: 68, last_run_at: new Date(Date.now() - 3600000 * 2).toISOString(), completed: 68, failed: 0, success_rate: 100, tokens: 88400 },
  { id: "Aegis", name: "Aegis", type: "SECURITY_ENGINEER", runs: 42, last_run_at: new Date(Date.now() - 3600000 * 3).toISOString(), completed: 42, failed: 0, success_rate: 100, tokens: 52100 },
  { id: "Atlas", name: "Atlas", type: "DATABASE_ENGINEER", runs: 38, last_run_at: new Date(Date.now() - 3600000 * 4).toISOString(), completed: 38, failed: 0, success_rate: 100, tokens: 49200 },
  { id: "Muse", name: "Muse", type: "FRONTEND_ENGINEER", runs: 34, last_run_at: new Date(Date.now() - 3600000 * 5).toISOString(), completed: 34, failed: 0, success_rate: 100, tokens: 29290 }
];

function getFakeDashboard(projectId) {
  const proj = FAKE_PROJECTS.find(p => p.id === projectId) || FAKE_PROJECTS[0];
  const activeMission = FAKE_MILESTONES.find(m => m.status === 'ACTIVE') || FAKE_MILESTONES[0];

  return {
    projects: FAKE_PROJECTS.map(p => ({ id: p.id, name: p.name, status: p.status })),
    project: {
      id: proj.id,
      name: proj.name,
      description: proj.description,
      status: proj.status
    },
    progress: {
      percent: 94,
      total_tasks: 78,
      completed_tasks: 73,
      remaining_tasks: 5
    },
    spend: {
      runs: 342,
      cost_usd: 14.82,
      tokens: 418290,
      budget_total: 1000000,
      budget_used: 418290
    },
    needs_attention: {
      count: 0,
      awaiting_approval: 0,
      failed_tasks: 0,
      blocked_tasks: 0
    },
    milestones: FAKE_MILESTONES,
    deliverables: FAKE_MILESTONES.filter(m => m.status === 'COMPLETED').slice(0, 6),
    current: {
      mission_id: activeMission.mission_id,
      title: activeMission.title,
      objective: activeMission.objective,
      status: activeMission.status,
      phase: activeMission.phase,
      progress: activeMission.progress,
      branch: activeMission.branch,
      created_at: activeMission.created_at,
      tasks_total: activeMission.tasks_total,
      tasks_done: activeMission.tasks_done,
      tasks: activeMission.tasks
    },
    team: FAKE_TEAM,
    reports: {
      by_phase: {
        "PLANNING": 12,
        "IMPLEMENTATION": 34,
        "VALIDATION": 18,
        "SECURITY": 8,
        "DELIVERY": 6
      }
    }
  };
}

// ── API Router ─────────────────────────────────────────────────────────────

export async function api(method, path, body) {
  // Support explicit query override (?live_db=true) if ever needed
  const isLiveRequested = window.location.search.includes('live_db=true');

  if (!isLiveRequested) {
    // 1. Dashboard summary & aggregated telemetry
    if (path.startsWith('/org/dashboard')) {
      const u = new URL('http://localhost' + path);
      const pid = u.searchParams.get('project_id') || FAKE_PROJECTS[0].id;
      return getFakeDashboard(pid);
    }

    if (path.startsWith('/dashboard/summary')) {
      return {
        user: { name: "Alex Vance", email: "alex@enterprise.cloud" },
        missions: { completed: 48, running: 1, needs_attention: 0 },
        avg_duration_seconds: 245
      };
    }

    // 2. Projects list
    if (path === '/projects') {
      return FAKE_PROJECTS;
    }

    // 3. Missions list
    if (path.startsWith('/missions/') && (path.includes('limit') || path === '/missions/')) {
      return FAKE_MILESTONES;
    }

    // 4. Mission detail
    if (path.startsWith('/missions/') && !path.includes('/diff') && !path.includes('/activity') && method === 'GET') {
      const mid = path.split('/')[2];
      const found = FAKE_MILESTONES.find(m => m.mission_id === mid) || FAKE_MILESTONES[0];
      return {
        id: found.mission_id,
        title: found.title,
        objective: found.objective,
        status: found.status,
        phase: found.phase,
        progress: found.progress,
        risk_level: "LOW",
        active_branch: found.branch,
        created_at: found.created_at,
        completed_at: found.completed_at,
        retry_count: 0,
        priority: "HIGH",
        milestones: [{ id: "m1", title: "Cleanroom Verification", status: "COMPLETED", progress: 100 }],
        tasks: found.tasks.map(t => ({
          id: t.id,
          title: t.title,
          status: t.status,
          phase: t.phase,
          agent_role: t.agent,
          attempt: 1
        })),
        delivery: {
          branch: found.branch,
          pr: found.pr,
          changesets: [{ files_changed: found.files_changed, lines_added: found.lines_added, lines_removed: found.lines_removed }]
        }
      };
    }

    // 5. Diff inspector
    if (path.includes('/diff')) {
      return {
        diff_text: `diff --git a/storage/wal.py b/storage/wal.py
new file mode 100644
index 0000000..9b62a4f
--- /dev/null
+++ b/storage/wal.py
@@ -0,0 +1,32 @@
+import struct
+import hashlib
+from typing import Optional
+
+class WriteAheadLog:
+    """High-throughput append-only WAL with CRC32 checksum verification."""
+    def __init__(self, filepath: str):
+        self.filepath = filepath
+        self._fd = open(filepath, "a+b", buffering=0)
+
+    def append(self, lsn: int, payload: bytes) -> int:
+        checksum = hashlib.crc32(payload)
+        header = struct.pack(">QII", lsn, len(payload), checksum)
+        self._fd.write(header + payload)
+        return self._fd.tell()
+
+    def recover(self):
+        """Deterministic replay verified against uncommitted checkpoints."""
+        self._fd.seek(0)
+        # Scan and verify AST invariants
+        return True
`,
        files: ["storage/wal.py", "storage/crc32.py", "tests/test_wal.py"],
        totals: { files: 3, added: 284, removed: 0 }
      };
    }

    // 6. Activity stream
    if (path.includes('/activity')) {
      return {
        events: [
          { type: "MISSION_COMPLETED", description: "Consensus verified across all 6 specialized agents", timestamp: new Date(Date.now() - 3600000 * 3.2).toISOString(), agent: "Axiom" },
          { type: "TEST_RUN", description: "183 Unit & Invariant Pytests Passed at 100%", timestamp: new Date(Date.now() - 3600000 * 3.5).toISOString(), agent: "Sentinel" },
          { type: "SECURITY_SCAN", description: "Zero OWASP vulnerabilities detected in AST audit", timestamp: new Date(Date.now() - 3600000 * 3.7).toISOString(), agent: "Aegis" },
          { type: "TASK_COMPLETED", description: "Binary frame serialization implemented", timestamp: new Date(Date.now() - 3600000 * 3.9).toISOString(), agent: "Atlas" },
          { type: "MISSION_CREATED", description: "Mission decomposed into DAG with 4 tasks", timestamp: new Date(Date.now() - 3600000 * 4).toISOString(), agent: "Axiom" }
        ]
      };
    }

    // 7. Team agents
    if (path.startsWith('/dashboard/agents') || path === '/ai') {
      return FAKE_TEAM;
    }

    // 8. Memory entries
    if (path.includes('/memory')) {
      return {
        memories: [
          { category: "architecture", key: "STORAGE_INVARIANT", value: "All database WAL records must be append-only with CRC32 frame checksums.", confidence: 0.98, source: "Atlas", created_at: new Date(Date.now() - 86400000).toISOString() },
          { category: "conventions", key: "SANDBOX_ISOLATION", value: "Code generation must occur in ephemeral cleanroom worktrees before main branch PR.", confidence: 1.0, source: "Axiom", created_at: new Date(Date.now() - 86400000 * 2).toISOString() },
          { category: "security", key: "SECRETS_POLICY", value: "Zero plaintext keys or credentials committed to git tree.", confidence: 1.0, source: "Aegis", created_at: new Date(Date.now() - 86400000 * 3).toISOString() }
        ]
      };
    }

    // 9. Interactive actions (Approve, Apply, Retry, Cancel, Start mission)
    if (method === 'POST') {
      if (path.includes('/missions/projects/') && path.includes('/missions')) {
        const newId = "demo-msn-" + Math.random().toString(36).substring(2, 7);
        return { mission_id: newId, status: "PLANNING" };
      }
      return { success: true, message: "Operation completed in isolated sandbox cleanroom." };
    }
  }

  // Fallback to real backend fetch if live_db=true
  const opts = { method, credentials: 'include', headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(`/api/v1${path}`, opts);
  } catch (e) {
    throw new ApiError(0, 'Cannot reach the Kobits server. Is it running?');
  }
  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    const detail = data && typeof data === 'object' ? (data.detail || JSON.stringify(data)) : (data || res.statusText);
    throw new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

export const get = (p) => api('GET', p);
export const post = (p, b = {}) => api('POST', p, b);
