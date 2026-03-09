import { Wallet, Timer, Star } from "lucide-react";
import type { SettlementStructure } from "../../types";

interface Props {
  structure: SettlementStructure;
}

export default function SettlementStructureViz({ structure }: Props) {
  const immediate = structure.immediate_payout_percent;
  const tranches = structure.performance_tranches || [];
  const trancheTotal = tranches.reduce((sum, t) => sum + t.percent, 0);
  const remainder = Math.max(0, 100 - immediate - trancheTotal);

  return (
    <div>
      <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">
        Settlement Structure
      </label>

      {/* Stacked bar */}
      <div className="flex h-6 rounded-lg overflow-hidden mt-1.5 border border-gray-200">
        {immediate > 0 && (
          <div
            className="bg-money flex items-center justify-center text-[10px] font-bold text-navy-900"
            style={{ width: `${immediate}%` }}
          >
            {immediate}%
          </div>
        )}
        {tranches.map((t, i) => (
          <div
            key={i}
            className="bg-navy-700 flex items-center justify-center text-[10px] font-bold text-white"
            style={{ width: `${t.percent}%` }}
          >
            {t.percent}%
          </div>
        ))}
        {remainder > 0 && (
          <div
            className="bg-gray-200 flex items-center justify-center text-[10px] font-bold text-gray-500"
            style={{ width: `${remainder}%` }}
          >
            {remainder}%
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="space-y-1.5 mt-2.5">
        <div className="flex items-start gap-2">
          <div className="w-2.5 h-2.5 rounded-sm bg-money mt-0.5 flex-shrink-0" />
          <div>
            <span className="text-xs font-medium text-gray-700 flex items-center gap-1">
              <Wallet className="w-3 h-3" />
              Immediate payout ({immediate}%)
            </span>
            <p className="text-[10px] text-gray-400">Released on verified delivery</p>
          </div>
        </div>

        {tranches.map((t, i) => (
          <div key={i} className="flex items-start gap-2">
            <div className="w-2.5 h-2.5 rounded-sm bg-navy-700 mt-0.5 flex-shrink-0" />
            <div>
              <span className="text-xs font-medium text-gray-700 flex items-center gap-1">
                <Timer className="w-3 h-3" />
                Performance tranche ({t.percent}%)
              </span>
              <p className="text-[10px] text-gray-400 leading-snug">
                {t.indicator}
                {t.escrow_duration_days && (
                  <> &middot; {t.escrow_duration_days}d escrow</>
                )}
                {t.partial_credit && <> &middot; partial credit</>}
              </p>
            </div>
          </div>
        ))}

        {structure.reputation_stake?.enabled && (
          <div className="flex items-start gap-2">
            <div className="w-2.5 h-2.5 rounded-sm bg-gray-200 mt-0.5 flex-shrink-0" />
            <div>
              <span className="text-xs font-medium text-gray-700 flex items-center gap-1">
                <Star className="w-3 h-3" />
                Reputation stake ({structure.reputation_stake.weight}x weight)
              </span>
              <p className="text-[10px] text-gray-400">
                Outcome accuracy weights the provider's EMA reputation score
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
