import AgentProfile from "@/components/marketplace-pages/AgentProfile";
import { AgentSoftwareApplicationJsonLd } from "@/components/seo/JsonLd";
import { fetchAgentById, fetchAgentsList } from "@/lib/api-server";

export const revalidate = 120;

export async function generateStaticParams() {
  try {
    const agents = await fetchAgentsList();
    return agents.map((a) => ({ botId: a.id }));
  } catch {
    return [];
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ botId: string }>;
}) {
  const { botId } = await params;
  const agent = await fetchAgentById(botId);
  if (!agent) return { title: "Agent" };
  return {
    title: agent.bot_name,
    description: (agent.description || `${agent.bot_name} on SettleBridge`).slice(
      0,
      160
    ),
    openGraph: {
      title: agent.bot_name,
      url: `https://market.settlebridge.ai/agents/${botId}`,
    },
  };
}

export default async function AgentProfilePage({
  params,
}: {
  params: Promise<{ botId: string }>;
}) {
  const { botId } = await params;
  const agent = await fetchAgentById(botId);

  return (
    <>
      {agent ? <AgentSoftwareApplicationJsonLd agent={agent} /> : null}
      <AgentProfile />
    </>
  );
}
