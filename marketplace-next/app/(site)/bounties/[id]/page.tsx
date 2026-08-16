import { BountyJobPostingJsonLd } from "@/components/seo/JsonLd";
import BountyDetail from "@/components/marketplace-pages/BountyDetail";
import { fetchBountyById } from "@/lib/api-server";

export const dynamic = "force-dynamic";

function stripMd(s: string): string {
  return s.replace(/[#*_`[\]]/g, "").slice(0, 160);
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  try {
    const bounty = await fetchBountyById(id);
    return {
      title: bounty.title,
      description: stripMd(bounty.description || bounty.title),
      openGraph: {
        title: bounty.title,
        description: stripMd(bounty.description || bounty.title),
        url: `https://market.settlebridge.ai/bounties/${id}`,
      },
    };
  } catch {
    return { title: "Bounty" };
  }
}

export default async function BountyDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let bounty = null as Awaited<ReturnType<typeof fetchBountyById>> | null;
  try {
    bounty = await fetchBountyById(id);
  } catch {
    bounty = null;
  }

  return (
    <>
      {bounty ? <BountyJobPostingJsonLd bounty={bounty} /> : null}
      <BountyDetail />
    </>
  );
}
