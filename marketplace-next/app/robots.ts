import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: [
          "/dashboard/",
          "/login",
          "/register",
          "/bounties/new",
          "/bounties/assist",
          "/contracts/new",
        ],
      },
    ],
    sitemap: "https://market.settlebridge.ai/sitemap.xml",
  };
}
