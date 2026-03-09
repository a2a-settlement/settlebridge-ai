import { Coins, Tag, BarChart3, Clock, Shield, FileText } from "lucide-react";
import type { BountyDraft } from "../../types";
import SettlementStructureViz from "./SettlementStructureViz";

const difficultyColors: Record<string, string> = {
  trivial: "bg-gray-100 text-gray-700",
  easy: "bg-green-100 text-green-800",
  medium: "bg-yellow-100 text-yellow-800",
  hard: "bg-orange-100 text-orange-800",
  expert: "bg-red-100 text-red-800",
};

interface Props {
  draft: BountyDraft | null;
}

export default function BountyDraftPreview({ draft }: Props) {
  if (!draft) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-400 px-6">
        <FileText className="w-12 h-12 mb-4 opacity-40" />
        <p className="text-sm font-medium mb-1">Bounty Preview</p>
        <p className="text-xs text-center">
          Your bounty will take shape here as the conversation progresses.
        </p>
      </div>
    );
  }

  const filledCount = [
    draft.title,
    draft.description,
    draft.category_slug,
    draft.reward_suggestion,
    draft.difficulty,
    draft.acceptance_criteria?.description,
    draft.settlement_structure,
  ].filter(Boolean).length;
  const completeness = Math.round((filledCount / 7) * 100);

  return (
    <div className="space-y-4 p-1">
      {/* Completeness bar */}
      <div>
        <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
          <span>Draft completeness</span>
          <span>{completeness}%</span>
        </div>
        <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-money rounded-full transition-all duration-500"
            style={{ width: `${completeness}%` }}
          />
        </div>
      </div>

      {/* Title */}
      <div>
        <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">
          Title
        </label>
        {draft.title ? (
          <h3 className="font-semibold text-navy-900 text-sm mt-0.5 leading-snug">
            {draft.title}
          </h3>
        ) : (
          <p className="text-xs text-gray-300 italic mt-0.5">Pending...</p>
        )}
      </div>

      {/* Description */}
      <div>
        <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">
          Description
        </label>
        {draft.description ? (
          <p className="text-xs text-gray-600 mt-0.5 leading-relaxed line-clamp-6">
            {draft.description}
          </p>
        ) : (
          <p className="text-xs text-gray-300 italic mt-0.5">Pending...</p>
        )}
      </div>

      {/* Meta row */}
      <div className="flex flex-wrap gap-2">
        {draft.reward_suggestion && (
          <span className="inline-flex items-center gap-1 text-xs font-bold bg-money/10 text-money-dark px-2.5 py-1 rounded-lg">
            <Coins className="w-3 h-3" />
            {draft.reward_suggestion} ATE
          </span>
        )}
        {draft.difficulty && (
          <span
            className={`text-xs font-medium px-2.5 py-1 rounded-full ${
              difficultyColors[draft.difficulty] || "bg-gray-100"
            }`}
          >
            {draft.difficulty}
          </span>
        )}
        {draft.category_slug && (
          <span className="text-xs text-gray-500 flex items-center gap-1 bg-gray-50 px-2 py-1 rounded-full">
            <Tag className="w-3 h-3" />
            {draft.category_slug.replace(/-/g, " ")}
          </span>
        )}
        {draft.provenance_tier && (
          <span className="text-xs text-gray-500 flex items-center gap-1 bg-gray-50 px-2 py-1 rounded-full">
            <Shield className="w-3 h-3" />
            {draft.provenance_tier.replace(/_/g, " ").replace("tier", "Tier ")}
          </span>
        )}
      </div>

      {/* Tags */}
      {draft.tags && draft.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {draft.tags.map((tag) => (
            <span
              key={tag}
              className="text-xs bg-navy-900/5 text-navy-700 px-2 py-0.5 rounded"
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* Acceptance Criteria */}
      {draft.acceptance_criteria?.description && (
        <div>
          <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">
            Acceptance Criteria
          </label>
          <p className="text-xs text-gray-600 mt-0.5 leading-relaxed">
            {draft.acceptance_criteria.description}
          </p>
          {draft.acceptance_criteria.output_format && (
            <span className="inline-block mt-1 text-xs bg-gray-50 text-gray-600 px-2 py-0.5 rounded">
              Format: {draft.acceptance_criteria.output_format}
            </span>
          )}
        </div>
      )}

      {/* Deadline */}
      {draft.deadline_suggestion && (
        <div className="flex items-center gap-1.5 text-xs text-gray-500">
          <Clock className="w-3 h-3" />
          {draft.deadline_suggestion}
        </div>
      )}

      {/* Settlement Structure */}
      {draft.settlement_structure && (
        <SettlementStructureViz structure={draft.settlement_structure} />
      )}
    </div>
  );
}
