import type { Bounty, ExchangeAgentSummary, ServiceContract } from "@/lib/types";

const SITE = "https://market.settlebridge.ai";

function scriptJson(data: Record<string, unknown>) {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data) }}
    />
  );
}

function stripMarkdown(s: string): string {
  return s.replace(/[#*_`[\]]/g, "").slice(0, 500);
}

export function BountyJobPostingJsonLd({ bounty }: { bounty: Bounty }) {
  const desc = stripMarkdown(bounty.description || bounty.title);
  const data = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    title: bounty.title,
    description: desc,
    datePosted: bounty.created_at,
    validThrough: bounty.deadline || undefined,
    employmentType: "CONTRACTOR",
    hiringOrganization: {
      "@type": "Organization",
      name: "SettleBridge Marketplace",
      sameAs: SITE,
    },
    baseSalary: {
      "@type": "MonetaryAmount",
      currency: "ATE",
      value: bounty.reward_amount,
    },
    jobLocationType: "TELECOMMUTE",
    url: `${SITE}/bounties/${bounty.id}`,
  };
  return scriptJson(data);
}

export function AgentSoftwareApplicationJsonLd({
  agent,
}: {
  agent: ExchangeAgentSummary;
}) {
  const data = {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: agent.bot_name,
    description: agent.description || `Agent ${agent.bot_name} on SettleBridge`,
    applicationCategory: "BusinessApplication",
    operatingSystem: "Any",
    url: `${SITE}/agents/${agent.id}`,
    ...(agent.reputation != null
      ? {
          aggregateRating: {
            "@type": "AggregateRating",
            ratingValue: Math.min(5, Math.max(1, agent.reputation * 5)),
            bestRating: 5,
            worstRating: 1,
            ratingCount: 1,
          },
        }
      : {}),
  };
  return scriptJson(data);
}

export function ContractServiceJsonLd({
  contract,
}: {
  contract: ServiceContract;
}) {
  const data = {
    "@context": "https://schema.org",
    "@type": "Service",
    name: contract.title,
    description: contract.description,
    provider: {
      "@type": "Organization",
      name: "SettleBridge Marketplace",
    },
    areaServed: "Worldwide",
    url: `${SITE}/contracts/${contract.id}`,
  };
  return scriptJson(data);
}
