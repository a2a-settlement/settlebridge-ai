import { TrendingUp, CheckCircle, Clock, ExternalLink } from "lucide-react";
import type { TrainingCardData } from "../types";

interface Props {
  run: TrainingCardData;
}

function ScoreChart({
  scores,
  threshold,
}: {
  scores: number[];
  threshold: number;
}) {
  if (scores.length === 0) {
    return (
      <div className="h-16 flex items-center justify-center text-xs text-gray-400">
        No iterations recorded
      </div>
    );
  }

  const W = 280;
  const H = 56;
  const padL = 2;
  const padR = 2;
  const padT = 4;
  const padB = 14;
  const chartW = W - padL - padR;
  const chartH = H - padT - padB;

  const n = scores.length;
  const gap = 2;
  const barW = Math.max(4, (chartW - gap * (n - 1)) / n);

  // Running EMA for overlay line
  const alpha = 2 / (Math.min(n, 10) + 1);
  const emaPoints: string[] = [];
  let runEma = 0;
  scores.forEach((s, i) => {
    runEma = i === 0 ? s : alpha * s + (1 - alpha) * runEma;
    const cx = padL + i * (barW + gap) + barW / 2;
    const cy = padT + chartH * (1 - runEma);
    emaPoints.push(`${cx.toFixed(1)},${cy.toFixed(1)}`);
  });

  const threshY = padT + chartH * (1 - threshold);

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full"
      style={{ height: H }}
    >
      {/* Bars */}
      {scores.map((s, i) => {
        const x = padL + i * (barW + gap);
        const bh = chartH * s;
        const y = padT + chartH - bh;
        const color =
          s >= threshold ? "#22c55e" : s >= threshold * 0.85 ? "#f59e0b" : "#ef4444";
        return (
          <g key={i}>
            <rect
              x={x}
              y={y}
              width={barW}
              height={bh}
              fill={color}
              rx={1.5}
              opacity={0.8}
            />
            <text
              x={x + barW / 2}
              y={H - 2}
              textAnchor="middle"
              fontSize={7}
              fill="#9ca3af"
            >
              {i + 1}
            </text>
          </g>
        );
      })}

      {/* Threshold line */}
      <line
        x1={padL}
        y1={threshY}
        x2={W - padR}
        y2={threshY}
        stroke="#f97316"
        strokeWidth={1.2}
        strokeDasharray="4 3"
      />

      {/* EMA line */}
      {emaPoints.length > 1 && (
        <polyline
          points={emaPoints.join(" ")}
          fill="none"
          stroke="#6366f1"
          strokeWidth={1.8}
          strokeLinejoin="round"
        />
      )}
    </svg>
  );
}

export default function TrainingRunCard({ run }: Props) {
  const isCompleted = run.status === "COMPLETED";
  const emaPct = `${(run.final_ema * 100).toFixed(1)}%`;
  const threshPct = `${(run.score_threshold * 100).toFixed(0)}%`;
  const cardUrl = `/api/training/runs/${run.run_id}/card.html`;
  const dateStr = run.completed_at
    ? new Date(run.completed_at).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : new Date(run.created_at).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      });

  return (
    <div className="bg-white border border-indigo-100 rounded-xl p-5 flex flex-col gap-3 hover:shadow-md transition-shadow">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-indigo-600 bg-indigo-50 border border-indigo-200 rounded-full px-2.5 py-0.5">
              <TrendingUp className="w-3 h-3" />
              Self-Improving
            </span>
            {isCompleted ? (
              <span className="inline-flex items-center gap-1 text-xs font-medium text-green-700 bg-green-50 rounded-full px-2 py-0.5">
                <CheckCircle className="w-3 h-3" />
                Completed
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs font-medium text-indigo-700 bg-indigo-50 rounded-full px-2 py-0.5">
                <Clock className="w-3 h-3" />
                Running
              </span>
            )}
          </div>
          <h3 className="font-semibold text-navy-900 text-sm leading-tight truncate">
            {run.public_title}
          </h3>
          <p className="text-xs text-gray-500 mt-0.5 truncate">
            {run.agent_display_name}
          </p>
        </div>
        <a
          href={cardUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-gray-400 hover:text-indigo-500 flex-shrink-0 mt-0.5"
          title="View full card"
        >
          <ExternalLink className="w-4 h-4" />
        </a>
      </div>

      {/* Chart */}
      <div className="bg-gray-50 rounded-lg px-2 pt-2 pb-1">
        <ScoreChart scores={run.scores} threshold={run.score_threshold} />
        <div className="flex justify-between items-center mt-1 px-0.5">
          <span className="text-xs text-indigo-500 font-medium flex items-center gap-1">
            <span className="inline-block w-3 h-0.5 bg-indigo-400 rounded" />
            EMA
          </span>
          <span className="text-xs text-orange-400 font-medium flex items-center gap-1">
            <span className="inline-block w-3 border-t border-dashed border-orange-400" />
            Threshold
          </span>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-2">
        <div className="text-center">
          <div
            className={`text-base font-bold ${
              run.threshold_reached ? "text-green-600" : "text-amber-500"
            }`}
          >
            {emaPct}
          </div>
          <div className="text-xs text-gray-400">Final EMA</div>
        </div>
        <div className="text-center">
          <div className="text-base font-bold text-navy-900">
            {run.iterations}
          </div>
          <div className="text-xs text-gray-400">Iterations</div>
        </div>
        <div className="text-center">
          <div className="text-base font-bold text-gray-600">{threshPct}</div>
          <div className="text-xs text-gray-400">Threshold</div>
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between pt-1 border-t border-gray-100">
        <span className="text-xs text-gray-400">{dateStr}</span>
        {run.merkle_root && (
          <span className="text-xs text-gray-400 font-mono truncate max-w-[120px]">
            {run.merkle_root.slice(0, 10)}…
          </span>
        )}
      </div>
    </div>
  );
}
