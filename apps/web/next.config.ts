import type { NextConfig } from "next";

// The browser only talks to this origin; /api is proxied server-side to FastAPI so the
// session cookie stays first-party and no backend credential ever reaches the bundle.
const API_URL = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_URL}/api/:path*` },
      { source: "/health/:path*", destination: `${API_URL}/health/:path*` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default config;
