/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/aws/:path*',
        destination: 'https://l6aogiqmxg.execute-api.us-east-1.amazonaws.com/prod/:path*',
      },
    ];
  },
};

export default nextConfig;
