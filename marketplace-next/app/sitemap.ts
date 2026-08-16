import type { MetadataRoute } from "next";
import {
  fetchAgentsList,
  fetchBountyIdsForSitemap,
  fetchContractIdsForSitemap,
} from "@/lib/api-server";

const base = "https://market.settlebridge.ai";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const staticRoutes: MetadataRoute.Sitemap = [
    { url: base, lastModified: new Date(), changeFrequency: "daily" },
    { url: `${base}/bounties`, changeFrequency: "hourly" },
    { url: `${base}/agents`, changeFrequency: "daily" },
    { url: `${base}/contracts`, changeFrequency: "hourly" },
    { url: `${base}/developers`, changeFrequency: "weekly" },
    { url: `${base}/demo`, changeFrequency: "monthly" },
  ];

  const [bountyRows, contractRows, agents] = await Promise.all([
    fetchBountyIdsForSitemap().catch(() => []),
    fetchContractIdsForSitemap().catch(() => []),
    fetchAgentsList().catch(() => []),
  ]);

  const bountyUrls: MetadataRoute.Sitemap = bountyRows.map((b) => ({
    url: `${base}/bounties/${b.id}`,
    lastModified: b.updated_at ? new Date(b.updated_at) : new Date(),
    changeFrequency: "weekly" as const,
  }));

  const contractUrls: MetadataRoute.Sitemap = contractRows.map((c) => ({
    url: `${base}/contracts/${c.id}`,
    lastModified: c.updated_at ? new Date(c.updated_at) : new Date(),
    changeFrequency: "weekly" as const,
  }));

  const agentUrls: MetadataRoute.Sitemap = agents.map((a) => ({
    url: `${base}/agents/${a.id}`,
    lastModified: a.created_at ? new Date(a.created_at) : new Date(),
    changeFrequency: "weekly" as const,
  }));

  return [...staticRoutes, ...bountyUrls, ...contractUrls, ...agentUrls];
}
