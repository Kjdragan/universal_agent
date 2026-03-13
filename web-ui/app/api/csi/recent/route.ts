import { NextResponse } from "next/server";

/**
 * GET /api/csi/recent
 *
 * Stub API route that returns mock CSI (Continuous Signal Intelligence) signals
 * for the Mission Control dashboard.
 * In production, this would be replaced by the gateway proxy to the backend.
 */
export async function GET() {
  const mockSignals = [
    {
      id: "csi-001",
      signal_type: "anomaly",
      source: "production-logs",
      title: "Unusual API latency spike",
      description: "Detected 3x increase in API response times",
      severity: "warning",
      created_at: new Date(Date.now() - 1800000).toISOString(),
      metadata: {
        endpoint: "/api/v1/users",
        latency_ms: 450,
        baseline_ms: 150,
      },
    },
    {
      id: "csi-002",
      signal_type: "event",
      source: "github-webhook",
      title: "New pull request merged",
      description: "PR #1234 merged to main branch",
      severity: "info",
      created_at: new Date(Date.now() - 3600000).toISOString(),
      metadata: {
        repository: "universal_agent",
        branch: "main",
        author: "developer",
      },
    },
    {
      id: "csi-003",
      signal_type: "alert",
      source: "monitoring",
      title: "Memory usage threshold exceeded",
      description: "Server memory usage above 85%",
      severity: "critical",
      created_at: new Date(Date.now() - 5400000).toISOString(),
      metadata: {
        server: "prod-web-01",
        memory_percent: 87,
        threshold: 85,
      },
    },
    {
      id: "csi-004",
      signal_type: "event",
      source: "scheduler",
      title: "Cron job completed",
      description: "Daily cleanup job finished successfully",
      severity: "info",
      created_at: new Date(Date.now() - 7200000).toISOString(),
      metadata: {
        job_name: "daily_cleanup",
        duration_seconds: 45,
        items_processed: 1234,
      },
    },
    {
      id: "csi-005",
      signal_type: "anomaly",
      source: "metrics",
      title: "Database query volume drop",
      description: "Query volume dropped 40% below baseline",
      severity: "warning",
      created_at: new Date(Date.now() - 10800000).toISOString(),
      metadata: {
        database: "primary",
        queries_per_minute: 500,
        baseline_qpm: 850,
      },
    },
  ];

  return NextResponse.json({
    status: "ok",
    items: mockSignals,
    count: mockSignals.length,
    window_hours: 24,
  });
}
