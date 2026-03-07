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
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: '/api/:path((?!dashboard/gateway).*)',
        destination: 'http://localhost:8002/api/:path',
      },
      {
        source: '/ws/:path*',
        destination: 'http://localhost:8002/ws/:path*',
      },
    ];
  },
};

module.exports = nextConfig;
