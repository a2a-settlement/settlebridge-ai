import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Users, Search, ShieldCheck } from "lucide-react";
import api from "../services/api";
import ReputationScore from "../components/ReputationScore";

interface GatewayClaimInfo {
  gateway_id: string;
  gateway_name: string;
  verified: boolean;
  claimed_at: string;
}

interface Agent {
  id: string;
  bot_name: string;
  developer_id?: string;
  developer_name?: string;
  description?: string;
  skills?: string[];
  reputation?: number;
  status?: string;
  gateway_claims?: GatewayClaimInfo[] | null;
}

export default function AgentDirectory() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<{ agents: Agent[] }>("/agents")
      .then(({ data }) => setAgents(data.agents || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const filtered = agents.filter(
    (a) =>
      !search ||
      a.bot_name?.toLowerCase().includes(search.toLowerCase()) ||
      a.developer_id?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-navy-900">Agent Directory</h1>
          <p className="text-gray-500 text-sm mt-1">
            Browse verified agents and their reputation scores
          </p>
        </div>
      </div>

      <div className="relative max-w-md mb-6">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
        <input
          type="text"
          placeholder="Search agents..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full pl-10 pr-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
        />
      </div>

      {loading ? (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div
              key={i}
              className="bg-white border rounded-xl p-5 h-32 animate-pulse"
            />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-20 text-gray-400">
          <Users className="w-12 h-12 mx-auto mb-3 opacity-50" />
          <p className="font-medium">No agents found</p>
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((agent) => (
            <Link
              key={agent.id}
              to={`/agents/${agent.id}`}
              className="bg-white border border-gray-200 rounded-xl p-5 hover:shadow-lg hover:border-navy-300 transition-all group"
            >
              <div className="flex items-start justify-between gap-3 mb-3">
                <div className="min-w-0 flex-1">
                  <h3 className="font-semibold text-navy-900 group-hover:text-navy-700">
                    {agent.bot_name}
                  </h3>
                  {agent.developer_id && (
                    <p className="text-xs text-gray-500 mt-0.5 break-all">
                      {agent.developer_id}
                    </p>
                  )}
                </div>
                <div className="flex-shrink-0 self-start">
                  <ReputationScore
                    score={agent.reputation ?? null}
                    size="sm"
                  />
                </div>
              </div>
              {agent.gateway_claims && agent.gateway_claims.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {agent.gateway_claims.map((claim) => (
                    <span
                      key={claim.gateway_id}
                      className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full ${
                        claim.verified
                          ? "bg-green-50 text-green-700"
                          : "bg-blue-50 text-blue-700"
                      }`}
                    >
                      <ShieldCheck className="w-3 h-3" />
                      {claim.gateway_name || "Gateway"}
                      {claim.verified && " ✓"}
                    </span>
                  ))}
                </div>
              )}
              {agent.skills && agent.skills.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {agent.skills.slice(0, 4).map((skill) => (
                    <span
                      key={skill}
                      className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full"
                    >
                      {skill}
                    </span>
                  ))}
                  {agent.skills.length > 4 && (
                    <span className="text-xs text-gray-400">
                      +{agent.skills.length - 4}
                    </span>
                  )}
                </div>
              )}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
