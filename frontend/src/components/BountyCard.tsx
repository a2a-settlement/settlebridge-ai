import { Link } from "react-router-dom";
import { Clock, Coins, Tag, BarChart3 } from "lucide-react";
import type { Bounty } from "../types";
import ProvenanceBadge from "./ProvenanceBadge";

const difficultyColors: Record<string, string> = {
  trivial: "bg-gray-100 text-gray-700",
  easy: "bg-green-100 text-green-800",
  medium: "bg-yellow-100 text-yellow-800",
  hard: "bg-orange-100 text-orange-800",
  expert: "bg-red-100 text-red-800",
};

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-600",
  open: "bg-money/10 text-money-dark",
  claimed: "bg-blue-100 text-blue-700",
  submitted: "bg-purple-100 text-purple-700",
  in_review: "bg-indigo-100 text-indigo-700",
  completed: "bg-green-100 text-green-800",
  disputed: "bg-red-100 text-red-700",
  expired: "bg-gray-100 text-gray-500",
  cancelled: "bg-gray-100 text-gray-500",
};

function timeUntil(deadline: string): string {
  const diff = new Date(deadline).getTime() - Date.now();
  if (diff <= 0) return "Expired";
  const days = Math.floor(diff / 86400000);
  if (days > 0) return `${days}d left`;
  const hours = Math.floor(diff / 3600000);
  return `${hours}h left`;
}

interface Props {
  bounty: Bounty;
}

export default function BountyCard({ bounty }: Props) {
  return (
    <Link
      to={`/bounties/${bounty.id}`}
      className="block bg-white border border-gray-200 rounded-xl p-5 hover:shadow-lg hover:border-navy-300 transition-all group"
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <h3 className="font-semibold text-navy-900 group-hover:text-navy-700 line-clamp-2 leading-snug">
          {bounty.title}
        </h3>
        <div className="flex-shrink-0 flex items-center gap-1.5 bg-money/10 text-money-dark px-3 py-1.5 rounded-lg font-bold text-sm">
          <Coins className="w-4 h-4" />
          {bounty.reward_amount} ATE
        </div>
      </div>

      <p className="text-gray-500 text-sm line-clamp-2 mb-4 leading-relaxed">
        {bounty.description}
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`text-xs font-medium px-2.5 py-1 rounded-full ${statusColors[bounty.status] ?? "bg-gray-100 text-gray-600"}`}
        >
          {bounty.status.replace("_", " ")}
        </span>
        <span
          className={`text-xs font-medium px-2.5 py-1 rounded-full ${difficultyColors[bounty.difficulty] ?? "bg-gray-100"}`}
        >
          {bounty.difficulty}
        </span>
        <ProvenanceBadge tier={bounty.provenance_tier} />
        {bounty.category && (
          <span className="text-xs text-gray-500 flex items-center gap-1">
            <Tag className="w-3 h-3" />
            {bounty.category.name}
          </span>
        )}
        {bounty.deadline && (
          <span className="text-xs text-gray-500 flex items-center gap-1 ml-auto">
            <Clock className="w-3 h-3" />
            {timeUntil(bounty.deadline)}
          </span>
        )}
      </div>
    </Link>
  );
}
