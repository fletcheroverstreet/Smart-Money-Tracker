import type { NextConfig } from "next";

// Where the Next.js server reaches the API. In Docker this is http://api:8000;
// locally the API runs on port 8000.
const apiInternal = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  // No floating Next.js badge in development: it sits on top of the phone layout's first tab
  // (and in the screenshots scripts/screenshots.py takes from the dev server).
  devIndicators: false,
  // In production Caddy sends /api/* straight to the API. Locally (no Caddy),
  // these rewrites make the browser's same-origin /api calls reach it too.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiInternal}/api/:path*` },
      { source: "/healthz", destination: `${apiInternal}/healthz` },
    ];
  },
};

export default nextConfig;
