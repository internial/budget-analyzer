/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/aws/:path*',
        destination: 'https://kl4vcw3lh0.execute-api.us-east-1.amazonaws.com/prod/:path*',
      },
    ];
  },
};

export default nextConfig;
