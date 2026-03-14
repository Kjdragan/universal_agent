import { NextResponse } from "next/server";

/**
 * GET /api/tasks
 *
 * Stub API route that returns mock task data for the Mission Control dashboard.
 * In production, this would be replaced by the gateway proxy to the backend.
 */
export async function GET() {
  const mockTasks = [
    {
      task_id: "task-001",
      title: "Review pull request #1234",
      description: "Code review for authentication module",
      status: "pending",
      priority: 3,
      project_key: "AUTH",
      labels: ["code-review", "urgent"],
      created_at: new Date(Date.now() - 3600000).toISOString(),
      updated_at: new Date(Date.now() - 1800000).toISOString(),
    },
    {
      task_id: "task-002",
      title: "Fix database connection timeout",
      description: "Investigate and resolve intermittent connection issues",
      status: "in_progress",
      priority: 4,
      project_key: "INFRA",
      labels: ["bug", "database"],
      created_at: new Date(Date.now() - 7200000).toISOString(),
      updated_at: new Date(Date.now() - 900000).toISOString(),
    },
    {
      task_id: "task-003",
      title: "Update API documentation",
      description: "Add missing endpoint documentation for v2 API",
      status: "queued",
      priority: 2,
      project_key: "DOCS",
      labels: ["documentation"],
      created_at: new Date(Date.now() - 86400000).toISOString(),
      updated_at: new Date(Date.now() - 43200000).toISOString(),
    },
    {
      task_id: "task-004",
      title: "Implement caching layer",
      description: "Add Redis caching for frequently accessed data",
      status: "pending",
      priority: 2,
      project_key: "PERF",
      labels: ["enhancement", "caching"],
      created_at: new Date(Date.now() - 172800000).toISOString(),
      updated_at: new Date(Date.now() - 86400000).toISOString(),
    },
    {
      task_id: "task-005",
      title: "Security audit follow-up",
      description: "Address findings from Q1 security audit",
      status: "completed",
      priority: 4,
      project_key: "SEC",
      labels: ["security", "audit"],
      created_at: new Date(Date.now() - 259200000).toISOString(),
      updated_at: new Date(Date.now() - 172800000).toISOString(),
    },
  ];

  return NextResponse.json({
    status: "ok",
    items: mockTasks,
    pagination: {
      total: mockTasks.length,
      offset: 0,
      limit: 10,
      has_more: false,
    },
  });
}
