import ContractDetail from "@/components/marketplace-pages/ContractDetail";
import { ContractServiceJsonLd } from "@/components/seo/JsonLd";
import { fetchContractById } from "@/lib/api-server";

export const revalidate = 60;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  try {
    const c = await fetchContractById(id);
    return {
      title: c.title,
      description: c.description.slice(0, 160),
      openGraph: {
        title: c.title,
        url: `https://market.settlebridge.ai/contracts/${id}`,
      },
    };
  } catch {
    return { title: "Contract" };
  }
}

export default async function ContractDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let contract = null;
  try {
    contract = await fetchContractById(id);
  } catch {
    contract = null;
  }

  return (
    <>
      {contract ? <ContractServiceJsonLd contract={contract} /> : null}
      <ContractDetail />
    </>
  );
}
