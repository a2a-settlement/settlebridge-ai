/**
 * Human-readable preview for JSON bounty deliverables.
 * Detects structured “analyst report” payloads; otherwise renders a sectioned document view.
 */

function tryParseJSONObject(raw: string): Record<string, unknown> | null {
  const t = raw.trim();
  if (!t.startsWith("{")) return null;
  try {
    const v = JSON.parse(t) as unknown;
    return v !== null && typeof v === "object" && !Array.isArray(v)
      ? (v as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function isStructuredReport(obj: Record<string, unknown>): boolean {
  return (
    typeof obj.title === "string" &&
    (obj.analyst_summary != null || obj.raw_data != null || obj.data_dictionary != null)
  );
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(v);
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function MetaRow({
  label,
  value,
  href,
}: {
  label: string;
  value: unknown;
  href?: boolean;
}) {
  const s = fmtValue(value);
  if (s === "—" || s === "") return null;
  return (
    <div className="text-sm">
      <span className="text-gray-500">{label}</span>
      {href && typeof value === "string" && value.startsWith("http") ? (
        <a
          href={value}
          target="_blank"
          rel="noreferrer"
          className="ml-2 text-navy-600 hover:underline break-all"
        >
          {value}
        </a>
      ) : (
        <span className="ml-2 font-medium text-navy-900 break-words">{s}</span>
      )}
    </div>
  );
}

function DirectionCard({
  label,
  data,
}: {
  label: string;
  data: Record<string, unknown>;
}) {
  const avg = asRecord(data.avg_returns);
  const horizons = avg
    ? ["1d", "5d", "10d", "20d", "30d"].map((h) => ({
        h,
        v: avg[h],
      }))
    : [];
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <h4 className="font-semibold text-navy-900 capitalize mb-2">{label}</h4>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
        <dt className="text-gray-500">Samples</dt>
        <dd className="font-medium">{fmtValue(data.samples)}</dd>
        <dt className="text-gray-500">Win rate</dt>
        <dd className="font-medium">
          {typeof data.win_rate === "number" ? `${data.win_rate}%` : fmtValue(data.win_rate)}
        </dd>
      </dl>
      {horizons.length > 0 && (
        <div className="mt-3 pt-3 border-t border-gray-100">
          <p className="text-xs text-gray-500 uppercase tracking-wide mb-2">Avg returns</p>
          <div className="grid grid-cols-5 gap-1 text-xs">
            {horizons.map(({ h, v }) => (
              <div key={h} className="text-center">
                <div className="text-gray-400">{h}</div>
                <div className="font-mono font-medium text-navy-800">
                  {v === null || v === undefined ? "—" : `${v}%`}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function fmtWinRate30d(n: number): string {
  if (n >= 0 && n <= 1) return `${(n * 100).toFixed(1)}%`;
  return `${n}%`;
}

function DirectionSubline(label: string, rec: Record<string, unknown>, borderClass: string) {
  return (
    <div className={`pl-2 border-l-2 ${borderClass}`}>
      {label}: {fmtValue(rec.samples)} @{" "}
      {typeof rec.win_rate === "number" ? `${rec.win_rate}%` : fmtValue(rec.win_rate)}
      {rec.avg_5d != null && (
        <span className="text-gray-500"> · 5d avg {fmtValue(rec.avg_5d)}%</span>
      )}
    </div>
  );
}

function SourceBreakdownCard({ name, block }: { name: string; block: Record<string, unknown> }) {
  const bear = asRecord(block.bearish);
  const bull = asRecord(block.bullish);
  const bearOnly = asRecord(block.bearish_only);
  const bullOnly = asRecord(block.bullish_only);
  return (
    <div className="rounded-lg border border-gray-100 bg-white p-3 text-sm">
      <div className="font-semibold text-navy-900 mb-2 capitalize">{name.replace(/_/g, " ")}</div>
      <div className="text-xs text-gray-600 space-y-1">
        <div>
          Total: <span className="font-medium text-navy-800">{fmtValue(block.total_samples)}</span>{" "}
          · Win rate:{" "}
          <span className="font-medium text-navy-800">
            {typeof block.win_rate === "number" ? `${block.win_rate}%` : fmtValue(block.win_rate)}
          </span>
        </div>
        {bear && DirectionSubline("Bearish", bear, "border-rose-200")}
        {bull && DirectionSubline("Bullish", bull, "border-emerald-200")}
        {bearOnly && DirectionSubline("Bearish", bearOnly, "border-rose-200")}
        {bullOnly && DirectionSubline("Bullish", bullOnly, "border-emerald-200")}
      </div>
    </div>
  );
}

function SignalWeightsTable({
  title,
  rows,
}: {
  title: string;
  rows: Record<string, unknown>[];
}) {
  if (!rows.length) return null;
  return (
    <div className="mt-4">
      <h4 className="text-sm font-semibold text-navy-900 mb-2">{title}</h4>
      <div className="overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-full text-xs">
          <thead>
            <tr className="bg-gray-50 text-left text-gray-600">
              <th className="px-3 py-2 font-medium">Source</th>
              <th className="px-3 py-2 font-medium">Signal</th>
              <th className="px-3 py-2 font-medium">30d win</th>
              <th className="px-3 py-2 font-medium">Weight</th>
              <th className="px-3 py-2 font-medium">n</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-gray-100 hover:bg-gray-50/80">
                <td className="px-3 py-2 font-medium text-navy-800">{fmtValue(r.source)}</td>
                <td className="px-3 py-2">{fmtValue(r.signal_type)}</td>
                <td className="px-3 py-2 font-mono">
                  {typeof r.win_rate_30d === "number"
                    ? fmtWinRate30d(r.win_rate_30d)
                    : fmtValue(r.win_rate_30d)}
                </td>
                <td className="px-3 py-2 font-mono">{fmtValue(r.confidence_weight)}</td>
                <td className="px-3 py-2 text-gray-600">{fmtValue(r.sample_size_30d)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DataDictionarySection({ dict }: { dict: Record<string, unknown> }) {
  return (
    <section className="mb-8">
      <h3 className="text-lg font-semibold text-navy-900 mb-3">Data dictionary</h3>
      <div className="rounded-xl border border-gray-200 bg-gray-50/50 p-4 space-y-4 text-sm">
        {Object.entries(dict).map(([k, v]) => {
          const sub = asRecord(v);
          if (sub && typeof v !== "string") {
            return (
              <div key={k}>
                <h4 className="font-medium text-navy-800 capitalize mb-2">
                  {k.replace(/_/g, " ")}
                </h4>
                <dl className="space-y-2 pl-3 border-l-2 border-navy-100">
                  {Object.entries(sub).map(([sk, sv]) => (
                    <div key={sk}>
                      <dt className="text-gray-500 text-xs uppercase tracking-wide">
                        {sk.replace(/_/g, " ")}
                      </dt>
                      <dd className="text-gray-800 mt-0.5">
                        {typeof sv === "string" ? sv : <GenericNested value={sv} depth={0} />}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            );
          }
          return (
            <div key={k}>
              <h4 className="font-medium text-navy-800 capitalize mb-1">
                {k.replace(/_/g, " ")}
              </h4>
              <p className="text-gray-700 whitespace-pre-wrap">{fmtValue(v)}</p>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function GenericNested({ value, depth }: { value: unknown; depth: number }) {
  if (value === null || value === undefined) return <span className="text-gray-400">—</span>;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return <span>{fmtValue(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-gray-400">[]</span>;
    if (value.every((x) => typeof x === "string")) {
      return (
        <ul className="list-disc list-inside space-y-1">
          {(value as string[]).map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ul>
      );
    }
    return (
      <ul className="space-y-2">
        {value.map((item, i) => (
          <li key={i} className={depth > 0 ? "text-sm" : ""}>
            <GenericNested value={item} depth={depth + 1} />
          </li>
        ))}
      </ul>
    );
  }
  const o = value as Record<string, unknown>;
  return (
    <dl
      className={`space-y-2 ${depth > 0 ? "pl-3 border-l border-gray-200" : ""}`}
    >
      {Object.entries(o).map(([k, v]) => (
        <div key={k}>
          <dt className="text-gray-500 text-xs">{k.replace(/_/g, " ")}</dt>
          <dd className="mt-0.5">
            <GenericNested value={v} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function GenericDocumentSections(obj: Record<string, unknown>) {
  const keys = Object.keys(obj);
  return (
    <div className="space-y-6">
      {keys.map((k) => (
        <section key={k}>
          <h3 className="text-sm font-semibold text-navy-900 capitalize mb-2 border-b border-gray-100 pb-1">
            {k.replace(/_/g, " ")}
          </h3>
          <div className="text-sm text-gray-800">
            <GenericNested value={obj[k]} depth={0} />
          </div>
        </section>
      ))}
    </div>
  );
}

function StructuredReportView({ report }: { report: Record<string, unknown> }) {
  const raw = asRecord(report.raw_data);
  const dir = raw ? asRecord(raw.directional_summary) : null;
  const bear = dir ? asRecord(dir.bearish) : null;
  const bull = dir ? asRecord(dir.bullish) : null;
  const sources = raw ? asRecord(raw.source_breakdown) : null;
  const weights = raw ? asRecord(raw.adaptive_signal_weights_30d) : null;
  const analyst = asRecord(report.analyst_summary);
  const dict = asRecord(report.data_dictionary);

  const topList = weights?.top_confidence_signals;
  const notableList = weights?.notable_high_volume;
  const topRows = Array.isArray(topList)
    ? (topList as Record<string, unknown>[])
    : [];
  const notableRows = Array.isArray(notableList)
    ? (notableList as Record<string, unknown>[])
    : [];

  return (
    <article className="text-gray-800">
      <header className="mb-8 pb-6 border-b border-gray-200">
        <h2 className="text-xl sm:text-2xl font-bold text-navy-900 leading-tight mb-4">
          {String(report.title)}
        </h2>
        <div className="grid sm:grid-cols-2 gap-3">
          <MetaRow label="Generated" value={report.generated_at} />
          <MetaRow label="Source agent" value={report.source_agent} />
          <MetaRow label="Agent card" value={report.agent_card} href />
          <MetaRow label="Query cost" value={report.query_cost_ate != null ? `${report.query_cost_ate} ATE` : null} />
          <MetaRow label="Settlement" value={report.settlement_method} />
          {typeof report.agent_endpoint === "string" && report.agent_endpoint && (
            <div className="text-sm sm:col-span-2">
              <span className="text-gray-500">A2A endpoint</span>
              <span className="ml-2 font-mono text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded break-all select-all">
                {String(report.agent_endpoint)}
              </span>
              <span className="ml-2 text-xs text-gray-400">(programmatic · POST only)</span>
            </div>
          )}
        </div>
      </header>

      {analyst && (
        <section className="mb-8">
          <h3 className="text-lg font-semibold text-navy-900 mb-3">Analyst summary</h3>
          {typeof analyst.headline === "string" && (
            <p className="text-base text-navy-800 font-medium mb-4 leading-relaxed">
              {analyst.headline}
            </p>
          )}
          {Array.isArray(analyst.key_findings) && (
            <ul className="list-disc list-outside ml-5 space-y-2 text-sm text-gray-700 mb-4">
              {(analyst.key_findings as unknown[])
                .filter((f): f is string => typeof f === "string")
                .map((f, i) => (
                  <li key={i} className="leading-relaxed">
                    {f}
                  </li>
                ))}
            </ul>
          )}
          {typeof analyst.actionable_takeaway === "string" && (
            <blockquote className="border-l-4 border-money pl-4 py-1 text-sm text-gray-700 bg-money/5 rounded-r-lg pr-3">
              {analyst.actionable_takeaway}
            </blockquote>
          )}
        </section>
      )}

      {dict && <DataDictionarySection dict={dict} />}

      {raw && (
        <section className="mb-8">
          <h3 className="text-lg font-semibold text-navy-900 mb-3">Results</h3>
          <div className="flex flex-wrap gap-4 mb-4 text-sm">
            {raw.symbol != null && (
              <span>
                <span className="text-gray-500">Symbol</span>{" "}
                <span className="font-bold text-navy-900">{fmtValue(raw.symbol)}</span>
              </span>
            )}
            {raw.total_resolved != null && (
              <span>
                <span className="text-gray-500">Resolved signals</span>{" "}
                <span className="font-bold text-navy-900">{fmtValue(raw.total_resolved)}</span>
              </span>
            )}
          </div>
          {bear && bull && (
            <div className="grid sm:grid-cols-2 gap-4 mb-6">
              <DirectionCard label="Bearish" data={bear} />
              <DirectionCard label="Bullish" data={bull} />
            </div>
          )}
          {sources && Object.keys(sources).length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-navy-900 mb-3">By source</h4>
              <div className="grid sm:grid-cols-2 gap-3">
                {Object.entries(sources).map(([name, block]) => {
                  const rec = asRecord(block);
                  if (!rec) return null;
                  return <SourceBreakdownCard key={name} name={name} block={rec} />;
                })}
              </div>
            </div>
          )}
          {weights && (
            <div className="mt-6 rounded-xl border border-gray-200 bg-gray-50/80 p-4">
              <h4 className="text-sm font-semibold text-navy-900 mb-1">
                Adaptive signal weights (30d)
              </h4>
              {typeof weights.scope === "string" && (
                <p className="text-xs text-gray-600 mb-3">{weights.scope}</p>
              )}
              <SignalWeightsTable title="Top confidence" rows={topRows} />
              <SignalWeightsTable title="Notable high volume" rows={notableRows} />
            </div>
          )}
          {raw.data_notes != null ? (
            <div className="mt-4 text-xs text-gray-600">
              <GenericNested value={raw.data_notes} depth={0} />
            </div>
          ) : null}
        </section>
      )}

      {typeof report.methodology === "string" && (
        <section className="mb-4">
          <h3 className="text-lg font-semibold text-navy-900 mb-2">Methodology</h3>
          <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">
            {report.methodology}
          </p>
        </section>
      )}
    </article>
  );
}

export default function JsonDeliverablePreview({ content }: { content: string }) {
  const obj = tryParseJSONObject(content);
  if (!obj) {
    return (
      <pre className="whitespace-pre-wrap text-xs font-mono leading-relaxed text-gray-700 overflow-x-auto">
        {content}
      </pre>
    );
  }

  if (isStructuredReport(obj)) {
    return <StructuredReportView report={obj} />;
  }

  return <GenericDocumentSections obj={obj} />;
}

export { tryParseJSONObject, isStructuredReport };
