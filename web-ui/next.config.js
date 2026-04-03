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
