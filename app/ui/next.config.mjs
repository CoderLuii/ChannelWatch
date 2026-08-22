import { PHASE_DEVELOPMENT_SERVER } from 'next/constants.js'

/** @param {string} phase */
export default function createNextConfig(phase) {
  const developmentApiOrigin = process.env.CHANNELWATCH_DEV_API_ORIGIN?.replace(/\/$/, '')
  const developmentServer = phase === PHASE_DEVELOPMENT_SERVER

  /** @type {import('next').NextConfig} */
  const nextConfig = {
    ...(developmentServer ? {} : { output: 'export' }),
    typescript: {
      ignoreBuildErrors: true,
    },
    images: {
      unoptimized: true,
    },
  }

  if (developmentServer && developmentApiOrigin) {
    nextConfig.rewrites = async () => [
      {
        source: '/api/:path*',
        destination: `${developmentApiOrigin}/api/:path*`,
      },
      {
        source: '/healthz/:path*',
        destination: `${developmentApiOrigin}/healthz/:path*`,
      },
      {
        source: '/metrics',
        destination: `${developmentApiOrigin}/metrics`,
      },
    ]
  }

  return nextConfig
}
