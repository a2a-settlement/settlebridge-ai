import { TrendingUp, CheckCircle, Clock, ExternalLink, ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";
import type { TrainingCardData } from "../types";
import { timeAgo, fullDateTime } from "../utils/time";

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
  const H = 90;
  const padL = 30;
  const padR = 4;
  const padT = 10;
  const padB = 16;
  const chartW = W - padL - padR;
  const chartH = H - padT - padB;

  // Auto-scale Y axis to the actual data range (include threshold)
  const allVals = [...scores, threshold];
  const rawMin = Math.min(...allVals);
  const rawMax = Math.max(...allVals);
  const dataRange = rawMax - rawMin;
  const padVal = Math.max(0.04, dataRange * 0.20);
  const yMin = Math.max(0, rawMin - padVal);
  const yMax = Math.min(1, rawMax + padVal);
  const yRange = Math.max(0.10, yMax - yMin);

  const toY = (v: number) => padT + chartH * (1 - (v - yMin) / yRange);
  const toBarH = (v: number) => chartH * (v - yMin) / yRange;

  const n = scores.length;
  const gap = Math.max(2, Math.min(6, chartW / (n * 4)));
  const barW = Math.max(6, (chartW - gap * (n - 1)) / n);

  // Grid ticks
  const tickCount = 4;
  const ticks = Array.from({ length: tickCount + 1 }, (_, ti) => yMin + (yRange * ti / tickCount));

  // EMA line
  const alpha = 2 / (Math.min(n, 10) + 1);
  const emaPoints: string[] = [];
  let runEma = 0;
  scores.forEach((s, i) => {
    runEma = i === 0 ? s : alpha * s + (1 - alpha) * runEma;
    const cx = padL + i * (barW + gap) + barW / 2;
    emaPoints.push(`${cx.toFixed(1)},${toY(runEma).toFixed(1)}`);
  });

  const threshY = toY(threshold);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}>
      {/* Grid lines and Y-axis labels */}
      {ticks.map((tick, ti) => {
        const ty = toY(tick);
        return (
          <g key={ti}>
            <line x1={padL} y1={ty} x2={W - padR} y2={ty} stroke="#e5e7eb" strokeWidth={0.8} />
            <text x={padL - 3} y={ty + 3} textAnchor="end" fontSize={7} fill="#9ca3af">
              {`${(tick * 100).toFixed(0)}%`}
            </text>
          </g>
        );
      })}

      {/* Bars + score labels + iteration numbers */}
      {scores.map((s, i) => {
        const x = padL + i * (barW + gap);
        const bh = toBarH(s);
        const by = toY(s);
        const color = s >= threshold ? "#22c55e" : s >= threshold * 0.85 ? "#f59e0b" : "#ef4444";
        const labelY = Math.max(padT + 8, by - 2);
        return (
          <g key={i}>
            <rect x={x} y={by} width={barW} height={bh} fill={color} rx={1.5} opacity={0.85} />
            <text x={x + barW / 2} y={labelY} textAnchor="middle" fontSize={7.5} fontWeight="600" fill={color}>
              {`${(s * 100).toFixed(0)}%`}
            </text>
            <text x={x + barW / 2} y={H - 3} textAnchor="middle" fontSize={7} fill="#9ca3af">
              {`#${i + 1}`}
            </text>
          </g>
        );
      })}

      {/* Threshold line */}
      <line x1={padL} y1={threshY} x2={W - padR} y2={threshY} stroke="#f97316" strokeWidth={1.2} strokeDasharray="4 3" />
      <text x={W - padR - 2} y={threshY - 2} textAnchor="end" fontSize={7} fill="#f97316" fontWeight="600">
        {`${(threshold * 100).toFixed(0)}% threshold`}
      </text>

      {/* EMA line */}
      {emaPoints.length > 1 && (
        <polyline
          points={emaPoints.join(" ")}
          fill="none"
          stroke="#6366f1"
          strokeWidth={1.8}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}
    </svg>
  );
}

export default function TrainingRunCard({ run }: Props) {
  const isCompleted = run.status === "COMPLETED";
  const lastScore = run.last_score ?? (run.scores.length > 0 ? run.scores[run.scores.length - 1] : null);
  const lastScorePct = lastScore !== null ? `${(lastScore * 100).toFixed(1)}%` : "—";
  const lastScoreColor = lastScore !== null && lastScore >= run.score_threshold ? "text-green-600" : "text-amber-500";
  const emaPct = `${(run.final_ema * 100).toFixed(1)}%`;
  const threshPct = `${(run.score_threshold * 100).toFixed(0)}%`;
  const cardUrl = `/api/training/runs/${run.run_id}/card.html`;
  const refDate = run.completed_at ?? run.created_at;
  const [showResult, setShowResult] = useState(false);
  const fs = run.final_submission;

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
          <div className={`text-base font-bold ${lastScoreColor}`}>
            {lastScorePct}
          </div>
          <div className="text-xs text-gray-400">Last Score</div>
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
      <div className="text-right text-xs text-gray-400 -mt-1">
        Smoothed EMA: {emaPct}
      </div>

      {/* Final result panel */}
      {fs && (
        <div className="border border-indigo-100 rounded-lg overflow-hidden">
          <button
            onClick={() => setShowResult((v) => !v)}
            className="w-full flex items-center justify-between px-3 py-2 bg-indigo-50 hover:bg-indigo-100 transition-colors text-left"
          >
            <div className="flex items-center gap-2">
              <CheckCircle className="w-3.5 h-3.5 text-indigo-500" />
              <span className="text-xs font-semibold text-indigo-700">Final Iteration Result</span>
              {fs.ai_score !== null && (
                <span
                  className={`text-xs font-bold px-1.5 py-0.5 rounded-full ${
                    fs.ai_score >= Math.round(run.score_threshold * 100)
                      ? "text-green-700 bg-green-100"
                      : "text-amber-700 bg-amber-100"
                  }`}
                >
                  {fs.ai_score}/100
                </span>
              )}
              {fs.ai_recommendation && (
                <span className="text-xs text-indigo-400 capitalize hidden sm:block">
                  {fs.ai_recommendation.replace(/_/g, " ")}
                </span>
              )}
            </div>
            {showResult ? (
              <ChevronUp className="w-3.5 h-3.5 text-indigo-400 flex-shrink-0" />
            ) : (
              <ChevronDown className="w-3.5 h-3.5 text-indigo-400 flex-shrink-0" />
            )}
          </button>

          {showResult && (
            <div className="px-3 py-3 bg-white flex flex-col gap-2">
              {fs.ai_notes && (
                <p className="text-xs text-gray-500 italic leading-relaxed">{fs.ai_notes}</p>
              )}
              {fs.content && (
                <pre className="text-xs text-gray-700 bg-gray-50 border border-gray-100 rounded p-2 whitespace-pre-wrap break-words max-h-48 overflow-y-auto leading-relaxed font-sans">
                  {fs.content.slice(0, 800)}{fs.content.length > 800 ? "…" : ""}
                </pre>
              )}
              <a
                href={`/bounties/${run.bounty_id}`}
                className="text-xs font-semibold text-indigo-600 hover:text-indigo-800 hover:underline self-end"
              >
                View full deliverable →
              </a>
            </div>
          )}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between pt-1 border-t border-gray-100 gap-2">
        <span
          className="text-xs text-gray-400 truncate"
          title={fullDateTime(refDate)}
        >
          {fullDateTime(refDate)}
        </span>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {run.merkle_root && (
            <span className="text-xs text-gray-400 font-mono hidden sm:block">
              {run.merkle_root.slice(0, 8)}…
            </span>
          )}
          <span className="text-xs font-medium text-indigo-500 bg-indigo-50 rounded-full px-2 py-0.5">
            {timeAgo(refDate)}
          </span>
        </div>
      </div>
    </div>
  );
}
