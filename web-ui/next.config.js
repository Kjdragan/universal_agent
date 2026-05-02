/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  turbopack: {
    root: __dirname,
  },
  async redirects() {
    return [
      {
        source: '/',
        destination: '/dashboard',
        permanent: false,
        missing: [
          { type: 'query', key: 'session_id' },
          { type: 'query', key: 'new_session' },
          { type: 'query', key: 'attach' },
        ],
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: '/api/vps/:path*',
        destination: 'http://localhost:8001/api/vps/:path*',
      },
      {
        source: '/api/artifacts/:path*',
        destination: 'http://localhost:8001/api/artifacts/:path*',
      },
      {
        source: '/api/files/:path*',
        destination: 'http://localhost:8001/api/files/:path*',
      },
      // Track B/C three-panel viewer routes — must forward to API server
      // (8001) where api/viewer_routes.py is mounted. Without this entry
      // the catch-all below sends them to Gateway (8002) which has no
      // viewer routes → 404. Order matters: this MUST come before the
      // catch-all.
      {
        source: '/api/viewer/:path*',
        destination: 'http://localhost:8001/api/viewer/:path*',
      },
      // Stripe Link payments routes — same rationale. Inert in production
      // until UA_ENABLE_LINK is set, but the proxy must be in place so
      // routes are reachable when the master switch flips.
      {
        source: '/api/link/:path*',
        destination: 'http://localhost:8001/api/link/:path*',
      },
      {
        source: '/api/:path((?!dashboard/gateway).*)',
        destination: 'http://localhost:8002/api/:path',
      },
      {
        source: '/ws/:path*',
        destination: 'http://localhost:8001/ws/:path*',
      },
    ];
  },
};

module.exports = nextConfig;
