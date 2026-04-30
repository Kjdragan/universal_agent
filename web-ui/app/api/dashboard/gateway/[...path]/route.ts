import { NextRequest, NextResponse } from "next/server";
import { getDashboardSessionFromCookies } from "@/lib/dashboardAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
]);

const DEFAULT_GATEWAY_PROXY_TOTAL_TIMEOUT_MS = 30000;
const DEFAULT_GATEWAY_PROXY_ATTEMPT_TIMEOUT_MS = 15000;

function boundedPositiveInt(value: string | undefined, fallback: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.min(Math.floor(parsed), max);
}

function gatewayProxyTotalTimeoutMs(): number {
  return boundedPositiveInt(
    process.env.UA_DASHBOARD_GATEWAY_PROXY_TOTAL_TIMEOUT_MS,
    DEFAULT_GATEWAY_PROXY_TOTAL_TIMEOUT_MS,
    30_000,
  );
}

function gatewayProxyAttemptTimeoutMs(): number {
  return boundedPositiveInt(
    process.env.UA_DASHBOARD_GATEWAY_PROXY_ATTEMPT_TIMEOUT_MS,
    DEFAULT_GATEWAY_PROXY_ATTEMPT_TIMEOUT_MS,
    gatewayProxyTotalTimeoutMs(),
  );
}

function gatewayBaseCandidates(): string[] {
  const configured = [
    (process.env.UA_DASHBOARD_GATEWAY_URL || "").trim(),
    (process.env.NEXT_PUBLIC_GATEWAY_URL || "").trim(),
    (process.env.UA_GATEWAY_URL || "").trim(),
  ].filter(Boolean);
  const fallback = ["http://127.0.0.1:8002", "http://localhost:8002"];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of [...configured, ...fallback]) {
    const normalized = raw.replace(/\/$/, "");
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
}

function isLocalGateway(url: URL): boolean {
  const host = (url.hostname || "").toLowerCase();
  return host === "127.0.0.1" || host === "localhost";
}

function gatewayOpsToken(): string {
  return (
    (process.env.UA_DASHBOARD_OPS_TOKEN || "").trim()
    || (process.env.UA_OPS_TOKEN || "").trim()
    || (process.env.NEXT_PUBLIC_UA_OPS_TOKEN || "").trim()
  );
}

/**
 * Check if dev-mode stub data should be used when upstream is unavailable.
 * Controlled by UA_DEV_MODE_STUBS env var (default: true in development).
 */
function isDevModeStubsEnabled(): boolean {
  const raw = (process.env.UA_DEV_MODE_STUBS || "").trim().toLowerCase();
  // Explicit opt-out
  if (["0", "false", "no", "off"].includes(raw)) return false;
  // Explicit opt-in
  if (["1", "true", "yes", "on"].includes(raw)) return true;
  // Default: enabled in development, disabled in production
  return process.env.NODE_ENV !== "production";
}

/**
 * Get stub data for a given API path when upstream is unavailable.
 * Used for development and testing when the backend gateway is not running.
 */
function getStubDataForPath(pathname: string): unknown | null {
  if (pathname === "/api/v1/dashboard/summary") {
    return {
      status: "dev_stub",
      active_agents: 3,
      tasks_completed_today: 12,
      error_rate: 0.05,
      system_load: 0.45
    };
  }

  if (pathname === "/api/v1/ops/sessions") {
    return {
      sessions: [
        {
          session_id: "stub-session-1",
          workspace_dir: "/tmp/stub",
          status: "active",
          source: "local",
          channel: "local",
          owner: "owner_primary",
          memory_mode: "direct_only",
          metadata: {},
        },
      ],
      total: 1,
      limit: 1,
      offset: 0,
    };
  }

  // CSI recent signals stub
  if (pathname === "/api/v1/csi/recent") {
    return {
      status: "ok",
      signals: [
        {
          id: "csi-001",
          title: "n8n automation demand surging on Freelancer.com",
          source: "opportunity_bundle",
          relevance_score: 92,
          mission_alignment: "freelance_monetization",
          created_at: new Date(Date.now() - 3600000).toISOString(),
        },
        {
          id: "csi-002",
          title: "AI agent development trending on X",
          source: "x_trends",
          relevance_score: 85,
          mission_alignment: "universal_agent",
          created_at: new Date(Date.now() - 7200000).toISOString(),
        },
        {
          id: "csi-003",
          title: "RAG implementation jobs increasing on Upwork",
          source: "csi_report",
          relevance_score: 78,
          mission_alignment: "freelance_monetization",
          created_at: new Date(Date.now() - 14400000).toISOString(),
        },
        {
          id: "csi-004",
          title: "r/MachineLearning discussing agent orchestration",
          source: "reddit",
          relevance_score: 65,
          mission_alignment: "universal_agent",
          created_at: new Date(Date.now() - 28800000).toISOString(),
        },
      ],
      total: 4,
    };
  }

  // System Resources stub (used by System Resources panel in Mission Control)
  if (pathname === "/api/v1/dashboard/system-resources") {
    return {
      version: 1,
      overall_status: "warn",
      generated_at_utc: new Date().toISOString(),
      summary: "Dev mode stub data. Connect to live gateway for real metrics.",
      metrics: {
        cpu_load_1m: 0.45,
        cpu_load_5m: 0.52,
        cpu_load_15m: 0.61,
        cpu_cores: 8,
        load_per_core: 0.06,
        ram_used_gb: 18.5,
        ram_total_gb: 31.0,
        ram_percent: 60,
        swap_used_gb: 2.1,
        swap_total_gb: 14.0,
        swap_percent: 15,
        disk_used_gb: 23,
        disk_total_gb: 183,
        disk_percent: 14,
        active_agent_sessions: 12,
        gateway_errors_30m: 0,
        dispatch_concurrency: 2,
      },
      findings: [],
    };
  }

  // CSI digests stub (used by CSI Signals panel)
  if (pathname === "/api/v1/dashboard/csi/digests") {
    return {
      digests: [
        {
          id: "digest-001",
          event_id: "evt-reddit-001",
          source: "reddit",
          event_type: "reddit_trending",
          title: "AI agent orchestration frameworks gaining traction on r/MachineLearning",
          summary: "Multiple threads discussing LangGraph vs CrewAI for production agent systems",
          created_at: new Date(Date.now() - 3600000).toISOString(),
        },
        {
          id: "digest-002",
          event_id: "evt-x-trends-001",
          source: "x_trends",
          event_type: "x_trends_brief",
          title: "AI-native consulting demand surging — small firms winning enterprise contracts",
          summary: "Trending discussion about how boutique AI firms are outpacing Big 4 in automation consulting",
          created_at: new Date(Date.now() - 10800000).toISOString(),
        },
        {
          id: "digest-003",
          event_id: "evt-rss-001",
          source: "rss_feed",
          event_type: "rss_batch",
          title: "Upwork introduces AI project matching — new opportunities for AI-native freelancers",
          summary: "Upwork rolling out AI-powered project recommendations based on skill profiles",
          created_at: new Date(Date.now() - 21600000).toISOString(),
        },
        {
          id: "digest-004",
          event_id: "evt-global-001",
          source: "global_brief",
          event_type: "global_batch",
          title: "n8n and Make.com automation demand up 40% on freelance platforms",
          summary: "Q1 2026 data shows sustained growth in workflow automation project postings",
          created_at: new Date(Date.now() - 43200000).toISOString(),
        },
      ],
    };
  }

  // Freelance Pipeline stub (used by Freelance Pipeline panel in Mission Control)
  if (pathname === "/api/v1/dashboard/freelance/pipeline") {
    return {
      opportunities: [
        {
          id: "opp-001",
          title: "Build AI chatbot for e-commerce customer support",
          platform: "Upwork",
          rate: "$80-120/hr",
          fit_score: 88,
          status: "new",
          created_at: new Date(Date.now() - 7200000).toISOString(),
        },
        {
          id: "opp-002",
          title: "RAG pipeline implementation for legal document search",
          platform: "Upwork",
          rate: "$100-150/hr",
          fit_score: 92,
          status: "qualified",
          created_at: new Date(Date.now() - 14400000).toISOString(),
        },
        {
          id: "opp-003",
          title: "n8n workflow automation for HR onboarding",
          platform: "Fiverr",
          rate: "$500 fixed",
          fit_score: 75,
          status: "new",
          created_at: new Date(Date.now() - 28800000).toISOString(),
        },
      ],
      applications: [
        {
          id: "app-001",
          position_title: "AI Automation Specialist — Remote",
          platform: "Upwork",
          company: "TechVenture Inc.",
          status: "submitted",
          created_at: new Date(Date.now() - 86400000).toISOString(),
        },
        {
          id: "app-002",
          position_title: "LangChain Developer for SaaS Product",
          platform: "Upwork",
          status: "response_received",
          created_at: new Date(Date.now() - 172800000).toISOString(),
        },
      ],
      stats: {
        total_opportunities: 3,
        active_applications: 2,
        draft_applications: 1,
        submitted_applications: 1,
        responses: 1,
        interviews: 0,
        success_rate: 0,
      },
    };
  }
  // Proactive Pipeline stub (used by Proactive Pipeline panel in Mission Control)
  if (pathname === "/api/v1/dashboard/proactive-pipeline") {
    return {
      pending_approvals: [
        {
          approval_id: "stub-approval-001",
          title: "Approve research decomposition: AI agent market analysis",
          status: "pending",
          task_id: "task-001",
          created_at: new Date(Date.now() - 1800000).toISOString(),
          source_kind: "brainstorm",
        },
      ],
      refinement_items: [
        {
          task_id: "task-refine-001",
          title: "Build competitive analysis pipeline for RAG startups",
          status: "refinement",
          source_kind: "csi",
          project_key: "research",
          priority: 2,
          labels: ["research", "competitive-analysis"],
          refinement_stage: "decomposition",
          updated_at: new Date(Date.now() - 600000).toISOString(),
          created_at: new Date(Date.now() - 7200000).toISOString(),
        },
        {
          task_id: "task-refine-002",
          title: "Set up n8n webhook integration for Upwork alerts",
          status: "refinement",
          source_kind: "email",
          project_key: "engineering",
          priority: 3,
          labels: ["code", "automation"],
          refinement_stage: "subtask_generation",
          updated_at: new Date(Date.now() - 900000).toISOString(),
          created_at: new Date(Date.now() - 14400000).toISOString(),
        },
      ],
      dispatch_queue: [
        {
          task_id: "task-dispatch-001",
          title: "Refactor YouTube playlist watcher error handling",
          status: "ready",
          source_kind: "manual",
          project_key: "engineering",
          priority: 1,
          labels: ["code", "refactor"],
          eligible: true,
          skip_reason: null,
          rank: 1,
          target_agent: "vp.coder.primary",
          routing_confidence: "label",
          routing_reason: "Coder label match: {'code', 'refactor'}",
          should_delegate: true,
          updated_at: new Date(Date.now() - 300000).toISOString(),
          created_at: new Date(Date.now() - 86400000).toISOString(),
        },
        {
          task_id: "task-dispatch-002",
          title: "Deep research: LangGraph vs CrewAI for production agent systems",
          status: "ready",
          source_kind: "csi",
          project_key: "research",
          priority: 2,
          labels: ["research", "deep-research"],
          eligible: true,
          skip_reason: null,
          rank: 2,
          target_agent: "vp.general.primary",
          routing_confidence: "label",
          routing_reason: "General label match: {'research', 'deep-research'}",
          should_delegate: true,
          updated_at: new Date(Date.now() - 1200000).toISOString(),
          created_at: new Date(Date.now() - 172800000).toISOString(),
        },
        {
          task_id: "task-dispatch-003",
          title: "Send weekly status update to Kevin",
          status: "ready",
          source_kind: "calendar",
          project_key: "",
          priority: 3,
          labels: ["communication"],
          eligible: false,
          skip_reason: "capacity_governor_backoff",
          rank: 3,
          target_agent: "simone",
          routing_confidence: "label",
          routing_reason: "Simone label match: {'communication'}",
          should_delegate: false,
          updated_at: new Date(Date.now() - 3600000).toISOString(),
          created_at: new Date(Date.now() - 86400000).toISOString(),
        },
      ],
      counts: {
        approvals: 1,
        refinement: 2,
        dispatch_eligible: 2,
        dispatch_total: 3,
      },
    };
  }
  if (pathname === "/api/v1/dashboard/proactive-signals") {
    return {
      status: "ok",
      sync: { youtube: 2, discord: 1 },
      cards: [
        {
          card_id: "stub-youtube-cluster",
          source: "youtube",
          card_type: "cluster",
          title: "YouTube topic cluster: agentic coding",
          summary: "Several non-Short videos are converging on agentic coding workflows. One transcript-backed sample is available and two metadata-only candidates look useful.",
          status: "pending",
          priority: 3,
          confidence_score: 0.74,
          novelty_score: 0.68,
          evidence: [
            { title: "Building agentic coding workflows", channel: "Small Educator", url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ", transcript_status: "ok" },
            { title: "Claude Code production patterns", channel: "Tool Builder", url: "https://www.youtube.com/watch?v=ysz5S6PUM-U", transcript_status: "missing" },
          ],
          actions: [
            { id: "research_further", label: "Research Further", description: "Create a follow-up research task." },
            { id: "fetch_transcripts", label: "Fetch More Transcripts", description: "Fetch representative transcripts." },
            { id: "add_to_knowledge", label: "Add To Knowledge", description: "Create a knowledge-note task." },
          ],
          feedback: { tag_counts: {}, history: [] },
          selected_action: {},
          created_at: new Date(Date.now() - 1800000).toISOString(),
          updated_at: new Date(Date.now() - 1800000).toISOString(),
        },
      ],
    };
  }
  // Agent Queue stub (used by Active Tasks panel in Mission Control)
  if (pathname === "/api/v1/dashboard/todolist/agent-queue") {
    return {
      status: "ok",
      items: [
        {
          task_id: "stub-task-001",
          title: "Refactor YouTube playlist watcher error handling",
          description: "Improve robustness of the playlist watcher to handle transient network failures gracefully.",
          project_key: "engineering",
          priority: 1,
          labels: ["code", "refactor"],
          status: "in_progress",
          must_complete: false,
          incident_key: null,
          score: 7.2,
          updated_at: new Date(Date.now() - 300000).toISOString(),
          due_at: new Date(Date.now() + 86400000).toISOString(),
          source_kind: "manual",
        },
        {
          task_id: "stub-task-002",
          title: "Deep research: LangGraph vs CrewAI for production agent systems",
          description: "Produce a comparative analysis of LangGraph and CrewAI for building production-grade multi-agent systems.",
          project_key: "research",
          priority: 2,
          labels: ["research", "deep-research"],
          status: "open",
          must_complete: false,
          incident_key: null,
          score: 6.8,
          updated_at: new Date(Date.now() - 1200000).toISOString(),
          due_at: null,
          source_kind: "csi",
        },
        {
          task_id: "stub-task-003",
          title: "Set up n8n webhook integration for Upwork alerts",
          description: "Create n8n workflow that receives Upwork job alert webhooks and routes them to the CSI pipeline.",
          project_key: "engineering",
          priority: 3,
          labels: ["code", "automation"],
          status: "blocked",
          must_complete: false,
          incident_key: null,
          score: 4.1,
          updated_at: new Date(Date.now() - 3600000).toISOString(),
          due_at: new Date(Date.now() + 172800000).toISOString(),
          source_kind: "email",
        },
        {
          task_id: "stub-task-004",
          title: "Send weekly status update to Kevin",
          description: "Compile and send the weekly status report covering agent metrics and pipeline progress.",
          project_key: "",
          priority: 3,
          labels: ["communication"],
          status: "completed",
          must_complete: false,
          incident_key: null,
          score: 1.0,
          updated_at: new Date(Date.now() - 86400000).toISOString(),
          due_at: null,
          source_kind: "calendar",
        },
      ],
      pagination: {
        total: 4,
        offset: 0,
        limit: 10,
        count: 4,
        has_more: false,
      },
    };
  }
  // Health endpoint stub (used by System Status panel in Mission Control)
  if (pathname === "/api/v1/health") {
    return {
      status: "healthy",
      timestamp: new Date().toISOString(),
      version: "dev-stub",
      db_status: "connected",
      db_error: null,
    };
  }

  // Capacity Governor stub (used by Capacity Governor panel in Mission Control)
  if (pathname === "/api/v1/dashboard/capacity") {
    return {
      max_concurrent: 5,
      active_slots: 2,
      available_slots: 3,
      in_backoff: false,
      backoff_remaining_seconds: 0,
      consecutive_429s: 0,
      total_429s: 3,
      total_requests: 142,
      total_shed: 1,
      last_429_at: new Date(Date.now() - 7200000).toISOString(),
    };
  }

  // Dashboard situations stub (used by Operator Brief panel in Mission Control)
  if (pathname === "/api/v1/dashboard/situations") {
    return {
      status: "ok",
      generated_at: new Date().toISOString(),
      source: "dev_stub",
      raw_events_href: "/dashboard/events",
      situations: [
        {
          id: "task:stub-task-003",
          kind: "task_situation",
          title: "Set up n8n webhook integration for Upwork alerts needs operator attention",
          summary: "Create n8n workflow that receives Upwork job alert webhooks and routes them to the CSI pipeline.",
          priority: "high",
          status: "blocked",
          requires_action: true,
          tags: ["task-hub", "email", "blocked", "p3", "engineering"],
          created_at_utc: new Date(Date.now() - 3600000).toISOString(),
          updated_at_utc: new Date(Date.now() - 3600000).toISOString(),
          source_domain: "task_hub",
          primary_href: "/dashboard/todolist?mode=agent&focus=stub-task-003",
          knowledge_block: {
            source: "task_hub",
            task_ids: ["stub-task-003"],
            event_ids: [],
            recommended_action: "Review the Task Hub state and unblock, approve, or route the next step.",
            handoff_prompt: "Assess this Universal Agent Task Hub situation and recommend the next action.\nTask: Set up n8n webhook integration for Upwork alerts\nStatus: blocked\nTask ID: stub-task-003",
          },
        },
        {
          id: "event:stub-evt-004",
          kind: "event_situation",
          title: "Cron email task failed",
          summary: "Weekly digest email failed to send: SMTP connection timeout",
          priority: "high",
          status: "new",
          requires_action: true,
          tags: ["cron", "cron-task-failed", "error", "needs-action"],
          created_at_utc: new Date(Date.now() - 7200000).toISOString(),
          updated_at_utc: new Date(Date.now() - 7200000).toISOString(),
          source_domain: "cron",
          primary_href: "/dashboard/events",
          knowledge_block: {
            source: "activity_event",
            task_ids: [],
            event_ids: ["stub-evt-004"],
            recommended_action: "Review and resolve the required operator action.",
            handoff_prompt: "Assess this Universal Agent situation and recommend the next action.\nTitle: Cron email task failed\nSummary: Weekly digest email failed to send: SMTP connection timeout\nSource: cron/cron_task_failed\nSeverity: error",
          },
        },
      ],
    };
  }

  // Dashboard events stub (used by raw Event Log and fallback testing)
  if (pathname === "/api/v1/dashboard/events") {
    return {
      events: [
        {
          id: "stub-evt-001",
          event_class: "heartbeat",
          source_domain: "heartbeat",
          kind: "heartbeat_complete",
          title: "Agent session completed successfully",
          summary: "CODIE finished task: Refactor YouTube playlist watcher error handling",
          severity: "success",
          status: "new",
          requires_action: false,
          created_at_utc: new Date(Date.now() - 300000).toISOString(),
          updated_at_utc: new Date(Date.now() - 300000).toISOString(),
        },
        {
          id: "stub-evt-002",
          event_class: "dispatch",
          source_domain: "csi",
          kind: "task_dispatched",
          title: "New CSI-sourced task dispatched",
          summary: "Deep research: LangGraph vs CrewAI routed to ATLAS",
          severity: "info",
          status: "new",
          requires_action: false,
          created_at_utc: new Date(Date.now() - 1200000).toISOString(),
          updated_at_utc: new Date(Date.now() - 1200000).toISOString(),
        },
        {
          id: "stub-evt-003",
          event_class: "capacity",
          source_domain: "heartbeat",
          kind: "capacity_backoff",
          title: "Capacity governor entered backoff",
          summary: "2 consecutive 429 errors triggered 60s backoff. Dispatch paused.",
          severity: "warning",
          status: "resolved",
          requires_action: false,
          created_at_utc: new Date(Date.now() - 3600000).toISOString(),
          updated_at_utc: new Date(Date.now() - 3400000).toISOString(),
        },
        {
          id: "stub-evt-004",
          event_class: "cron",
          source_domain: "cron",
          kind: "cron_task_failed",
          title: "Cron email task failed",
          summary: "Weekly digest email failed to send: SMTP connection timeout",
          severity: "error",
          status: "new",
          requires_action: true,
          created_at_utc: new Date(Date.now() - 7200000).toISOString(),
          updated_at_utc: new Date(Date.now() - 7200000).toISOString(),
        },
        {
          id: "stub-evt-005",
          event_class: "refinement",
          source_domain: "continuity",
          kind: "task_decomposed",
          title: "Task decomposed into 4 subtasks",
          summary: "Build competitive analysis pipeline broken into research, scrape, analyze, report stages",
          severity: "info",
          status: "new",
          requires_action: false,
          created_at_utc: new Date(Date.now() - 14400000).toISOString(),
          updated_at_utc: new Date(Date.now() - 14400000).toISOString(),
        },
      ],
    };
  }

  return null;
}

function buildUpstreamHeaders(request: NextRequest, ownerId: string): Headers {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (HOP_BY_HOP.has(lower)) return;
    if (lower.startsWith("x-forwarded-")) return;
    headers.set(key, value);
  });

  headers.set("x-ua-dashboard-owner", ownerId);
  const opsToken = gatewayOpsToken();
  if (opsToken) {
    headers.set("x-ua-ops-token", opsToken);
    headers.set("authorization", `Bearer ${opsToken}`);
  }
  return headers;
}

function maybeApplyOwnerFilter(pathname: string, params: URLSearchParams, ownerId: string): void {
  const enforceRaw = (process.env.UA_DASHBOARD_ENFORCE_OWNER_FILTER || "").trim().toLowerCase();
  const enforceOwnerFilter = ["1", "true", "yes", "on"].includes(enforceRaw);
  if (!enforceOwnerFilter) return;
  if (params.has("owner")) return;
  if (!ownerId) return;

  if (pathname === "/api/v1/ops/sessions" || pathname === "/api/v1/ops/calendar/events") {
    params.set("owner", ownerId);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchWithTransientRetry(
  upstreamUrl: URL,
  init: RequestInit,
  method: string,
  deadlineMs: number,
): Promise<Response> {
  const upperMethod = method.toUpperCase();
  const retryable = upperMethod === "GET" || upperMethod === "HEAD";
  const maxAttempts = retryable ? 2 : 1;
  let lastError: unknown = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const remainingMs = deadlineMs - Date.now();
    if (remainingMs <= 0) break;

    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(
        () => controller.abort(),
        Math.min(gatewayProxyAttemptTimeoutMs(), remainingMs),
      );
      try {
        const res = await fetch(upstreamUrl, {
          ...init,
          signal: controller.signal,
        });
        return res;
      } finally {
        clearTimeout(timeoutId);
      }
    } catch (err: any) {
      lastError = err;
      if (err.name === 'AbortError') {
         lastError = new Error("Gateway timeout connecting to backend.");
      }
      if (attempt >= maxAttempts) break;
      // Transient gateway restarts can drop localhost:8002 briefly.
      const retryDelayMs = Math.min(120 * attempt, Math.max(0, deadlineMs - Date.now()));
      if (retryDelayMs <= 0) break;
      await sleep(retryDelayMs);
    }
  }

  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

async function proxyRequest(request: NextRequest, path: string[]) {
  const session = await getDashboardSessionFromCookies();
  if (!session.authenticated && session.authRequired) {
    return NextResponse.json({ detail: "Dashboard login required." }, { status: 401 });
  }

  const safePath = path.map((segment) => encodeURIComponent(segment)).join("/");
  const upstreamPathname = `/${safePath}`;

  // Fast-path: Return stubs immediately if enabled to prevent hanging the connection
  // pool and blocking client-side transitions due to slow Python 404 responses.
  if (isDevModeStubsEnabled()) {
    const stubData = getStubDataForPath(upstreamPathname);
    if (stubData) {
      return NextResponse.json(stubData);
    }
  }

  const queryEntries = Array.from(request.nextUrl.searchParams.entries());

  const headers = buildUpstreamHeaders(request, session.ownerId);
  const method = request.method.toUpperCase();
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();

  const candidates = gatewayBaseCandidates();
  const deadlineMs = Date.now() + gatewayProxyTotalTimeoutMs();
  let upstreamResponse: Response | null = null;
  let upstreamUrl: URL | null = null;
  let lastFetchError: unknown = null;
  for (const base of candidates) {
    if (Date.now() >= deadlineMs) break;
    const candidateUrl = new URL(`${base}${upstreamPathname}`);
    for (const [key, value] of queryEntries) {
      candidateUrl.searchParams.set(key, value);
    }
    maybeApplyOwnerFilter(upstreamPathname, candidateUrl.searchParams, session.ownerId);
    try {
      const response = await fetchWithTransientRetry(
        candidateUrl,
        {
          method,
          headers,
          body,
          cache: "no-store",
          redirect: "manual",
        },
        method,
        deadlineMs,
      );
      const contentType = (response.headers.get("content-type") || "").toLowerCase();
      const shouldTryNext =
        !isLocalGateway(candidateUrl)
        && response.status >= 500
        && response.status <= 504
        && (
          contentType.includes("text/html")
          || response.status === 502
          || response.status === 503
          || response.status === 504
        );
      upstreamResponse = response;
      upstreamUrl = candidateUrl;
      if (shouldTryNext) {
        continue;
      }
      break;
    } catch (err) {
      lastFetchError = err;
      continue;
    }
  }

  // Dev-mode fallback: return stub data if upstream is unavailable (when enabled)
  if (!upstreamResponse || !upstreamUrl) {
    if (isDevModeStubsEnabled()) {
      const stubData = getStubDataForPath(upstreamPathname);
      if (stubData) {
        return NextResponse.json(stubData);
      }
    }
    return NextResponse.json(
      {
        detail: "Gateway upstream unavailable.",
        upstream: `${candidates[0] || "http://localhost:8002"}${upstreamPathname}`,
        error: lastFetchError instanceof Error ? lastFetchError.message : String(lastFetchError),
      },
      { status: 502 },
    );
  }

  const upstreamContentType = (upstreamResponse.headers.get("content-type") || "").toLowerCase();
  if (upstreamResponse.status >= 500 && upstreamContentType.includes("text/html")) {
    // Dev-mode fallback: return stub data if upstream returns 500+ HTML error (when enabled)
    if (isDevModeStubsEnabled()) {
      const stubData = getStubDataForPath(upstreamPathname);
      if (stubData) {
        return NextResponse.json(stubData);
      }
    }
    const htmlSnippet = (await upstreamResponse.text().catch(() => "")).replace(/\s+/g, " ").trim().slice(0, 180);
    return NextResponse.json(
      {
        detail: "Gateway upstream returned an invalid HTML error response.",
        upstream: upstreamUrl.toString(),
        status: upstreamResponse.status,
        error: htmlSnippet || "Upstream error",
      },
      { status: upstreamResponse.status },
    );
  }

  // Dev-mode fallback: return stub data when upstream returns 404 for new endpoints
  if (upstreamResponse.status === 404 && isDevModeStubsEnabled()) {
    const stubData = getStubDataForPath(upstreamPathname);
    if (stubData) {
      return NextResponse.json(stubData);
    }
  }

  const responseHeaders = new Headers();
  upstreamResponse.headers.forEach((value, key) => {
    if (HOP_BY_HOP.has(key.toLowerCase())) return;
    responseHeaders.set(key, value);
  });
  responseHeaders.set("cache-control", "no-store");

  return new NextResponse(upstreamResponse.body, {
    status: upstreamResponse.status,
    headers: responseHeaders,
  });
}

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path || []);
}

export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path || []);
}

export async function PUT(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path || []);
}

export async function PATCH(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path || []);
}

export async function DELETE(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path || []);
}
