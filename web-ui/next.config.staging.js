// Staging-specific Next.js config.
// At deploy time, deploy-staging.yml copies this file over next.config.js
// inside /opt/universal-agent-staging/web-ui so the staging Next.js server
// proxies API calls to the staging API/gateway bridge (port 9001) and the
// gateway-backed REST surface (port 9002).
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Expose the runtime stage so the UI can show a staging banner
  env: {
    NEXT_PUBLIC_UA_RUNTIME_STAGE: 'staging',
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
        // Proxy all /api/* calls to the STAGING gateway (port 9002)
        source: '/api/:path((?!dashboard/gateway).*)',
        destination: 'http://localhost:9002/api/:path',
      },
      {
        source: '/ws/:path*',
        destination: 'http://localhost:9001/ws/:path*',
      },
    ];
  },
};

module.exports = nextConfig;
