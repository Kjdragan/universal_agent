import { NextResponse } from "next/server";

/**
 * GET /api/health
 *
 * Stub API route that returns mock health status for the Mission Control dashboard.
 * In production, this would be replaced by the gateway proxy to the backend.
 */
export async function GET() {
  const healthStatus = {
    status: "healthy",
    timestamp: new Date().toISOString(),
    version: "2.0.0",
    uptime_seconds: Math.floor(process.uptime?.() || 86400),
    components: {
      database: {
        status: "connected",
        latency_ms: 12,
      },
      cache: {
        status: "connected",
        latency_ms: 2,
      },
      queue: {
        status: "operational",
        pending_jobs: 5,
      },
    },
    checks: [
      {
        name: "database_connection",
        status: "pass",
        message: "PostgreSQL connection pool healthy",
      },
      {
        name: "redis_connection",
        status: "pass",
        message: "Redis cache responding",
      },
      {
        name: "disk_space",
        status: "pass",
        message: "42% disk space available",
      },
      {
        name: "memory",
        status: "warning",
        message: "Memory usage at 72%",
      },
    ],
  };

  return NextResponse.json(healthStatus);
}
