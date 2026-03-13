function scoreColor(score: number): string {
  if (score >= 0.7) return "text-green-600";
  if (score >= 0.4) return "text-yellow-600";
  return "text-red-600";
}

function barColor(score: number): string {
  if (score >= 0.7) return "bg-green-500";
  if (score >= 0.4) return "bg-yellow-500";
  return "bg-red-500";
}

export default function ReputationGauge({
  score,
  showBar = true,
}: {
  score: number | null;
  showBar?: boolean;
}) {
  if (score === null) {
    return <span className="text-gray-400 text-sm">N/A</span>;
  }

  return (
    <div className="flex items-center gap-2">
      <span className={`text-sm font-semibold tabular-nums ${scoreColor(score)}`}>
        {score.toFixed(2)}
      </span>
      {showBar && (
        <div className="w-16 h-1.5 bg-gray-200 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${barColor(score)}`}
            style={{ width: `${Math.min(score * 100, 100)}%` }}
          />
        </div>
      )}
    </div>
  );
}
