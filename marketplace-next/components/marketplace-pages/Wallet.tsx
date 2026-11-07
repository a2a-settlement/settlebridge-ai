"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import axios from "axios";
import { AlertCircle, Download, Loader2, Wallet as WalletIcon } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import api from "@/services/api";

interface Balance {
  available: number;
  held_in_escrow: number;
  total_earned: number;
  total_spent: number;
}

interface HeldBreakdown {
  bounty_holds: number;
  other_holds: number;
  unexplained: number;
}

type BountyRole = "funder" | "solver";
type BountyState =
  | "advertised"
  | "reserved"
  | "settling"
  | "paid"
  | "refunded"
  | "cancelled";

interface LedgerEntry {
  kind: "github_bounty";
  role: BountyRole;
  bounty_id: string;
  repo: string;
  issue_number: number;
  title: string;
  pr_number: number | null;
  state: BountyState;
  amount_ate: number;
  fee_ate: number;
  escrow_id: string | null;
  escrow_status: string | null;
  updated_at: string;
  issue_url: string;
  pr_url: string | null;
}

interface OtherEscrow {
  escrow_id: string;
  role: "requester" | "provider";
  counterparty_id: string;
  counterparty_name: string | null;
  amount: number;
  fee_amount: number;
  status: string;
  held_ate: number;
  efficacy_check_at: string | null;
  task_id: string | null;
  created_at: string;
}

interface LedgerResponse {
  account_id: string | null;
  balance: Balance;
  committed_ate: number;
  free_ate: number;
  held_breakdown: HeldBreakdown;
  entries: LedgerEntry[];
  other_escrows: OtherEscrow[];
  exchange_error: string | null;
}

const STATE_STYLES: Record<string, string> = {
  advertised: "bg-blue-100 text-blue-700",
  reserved: "bg-purple-100 text-purple-700",
  settling: "bg-amber-100 text-amber-800",
  paid: "bg-green-100 text-green-800",
  refunded: "bg-gray-100 text-gray-600",
  cancelled: "bg-red-100 text-red-700",
};

function messageFrom(err: unknown, fallback: string): string {
  if (!axios.isAxiosError(err)) return fallback;
  const detail = err.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  return fallback;
}

function formatCount(value: number): string {
  if (!Number.isFinite(value)) return "0";
  return value.toLocaleString();
}

function formatAte(value: number): string {
  return `${formatCount(value)} ATE (test)`;
}

function formatWhen(value: string, dateOnly = false): string {
  if (!value.trim()) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  if (dateOnly) return date.toLocaleDateString();
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function safeHttpUrl(value: string | null): string | null {
  if (!value?.trim()) return null;
  try {
    const url = new URL(value);
    if (url.protocol === "https:" || url.protocol === "http:") return url.toString();
  } catch {
    return null;
  }
  return null;
}

function shortId(id: string): string {
  const trimmed = id.trim();
  if (!trimmed) return "Unknown";
  if (trimmed.length <= 8) return trimmed;
  return `${trimmed.slice(0, 8)}…`;
}

function counterpartyLabel(escrow: OtherEscrow): { text: string; isId: boolean } {
  const name = escrow.counterparty_name?.trim();
  if (name) return { text: name, isId: false };
  return { text: shortId(escrow.counterparty_id), isId: true };
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="bg-red-50 text-red-700 rounded-lg p-3 flex items-center gap-2 text-sm">
      <AlertCircle className="w-4 h-4 flex-shrink-0" /> {message}
    </div>
  );
}

function SummaryCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: ReactNode;
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-navy-900">{value}</p>
      {detail}
    </div>
  );
}

function ExternalAnchor({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-navy-600 hover:underline"
    >
      {children}
    </a>
  );
}

const ACCOUNT_KIND_NOTE = {
  test: {
    label: "Test account",
    className:
      "text-xs font-medium px-2 py-0.5 rounded-full bg-amber-50 text-amber-800 border border-amber-200",
    detail:
      "Payments to this account came from your own funder account, so they are not outside reputation.",
  },
  house: {
    label: "House agent",
    className:
      "text-xs font-medium px-2 py-0.5 rounded-full bg-navy-100 text-navy-800 border border-navy-200",
    detail:
      "Operated by SettleBridge. It can claim a bounty only after it has gone unclaimed for 24 hours.",
  },
} as const;

function AccountKindNote({ kind }: { kind: "test" | "house" | null | undefined }) {
  if (kind !== "test" && kind !== "house") return null;
  const note = ACCOUNT_KIND_NOTE[kind];
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className={note.className}>{note.label}</span>
      <p className="text-sm text-gray-600">{note.detail}</p>
    </div>
  );
}

export default function Wallet() {
  const { user } = useAuth();
  const [ledger, setLedger] = useState<LedgerResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const { data } = await api.get<LedgerResponse>("/v1/me/ledger");
      setLedger(data);
    } catch (err: unknown) {
      setLedger(null);
      setError(messageFrom(err, "Couldn't load your wallet."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const downloadCsv = async () => {
    if (downloading) return;
    setDownloading(true);
    setDownloadError("");
    try {
      const { data } = await api.get<Blob>("/v1/me/ledger.csv", { responseType: "blob" });
      const url = URL.createObjectURL(data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "ledger.csv";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err: unknown) {
      setDownloadError(messageFrom(err, "Couldn't download the ledger."));
    } finally {
      setDownloading(false);
    }
  };

  const downloadButton = (
    <button
      type="button"
      onClick={() => void downloadCsv()}
      disabled={downloading}
      className="inline-flex items-center gap-2 px-4 py-2 bg-white text-gray-700 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50"
    >
      {downloading ? (
        <Loader2 className="w-4 h-4 animate-spin" />
      ) : (
        <Download className="w-4 h-4" />
      )}
      {downloading ? "Downloading…" : "Download CSV"}
    </button>
  );

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-10 h-10 rounded-xl bg-navy-900 flex items-center justify-center flex-shrink-0">
            <WalletIcon className="w-5 h-5 text-money" />
          </div>
          <div>
            <h1 className="font-semibold text-navy-900">Wallet</h1>
            <p className="text-xs text-gray-500">
              Test ATE balance, bounty activity, and escrow holds.
            </p>
          </div>
        </div>
        {downloadButton}
      </div>

      <AccountKindNote kind={user?.account_kind} />

      {downloadError && <ErrorBox message={downloadError} />}

      {loading && (
        <p className="text-sm text-gray-400 flex items-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading…
        </p>
      )}

      {!loading && error && (
        <div className="space-y-3">
          <ErrorBox message={error} />
          <button
            type="button"
            onClick={() => void load()}
            className="text-sm font-medium text-navy-700 hover:text-navy-900"
          >
            Try again
          </button>
        </div>
      )}

      {!loading && ledger && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <SummaryCard label="Available" value={formatAte(ledger.balance.available)} />
            <SummaryCard
              label="Held in escrow"
              value={formatAte(ledger.balance.held_in_escrow)}
              detail={
                <>
                  <p className="text-xs text-gray-500 mt-2">
                    Bounty holds {formatAte(ledger.held_breakdown.bounty_holds)}, other holds{" "}
                    {formatAte(ledger.held_breakdown.other_holds)}
                  </p>
                  {ledger.held_breakdown.unexplained !== 0 && (
                    <p className="mt-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5">
                      {formatAte(ledger.held_breakdown.unexplained)} of the held balance is not
                      explained by these rows.
                    </p>
                  )}
                </>
              }
            />
            <SummaryCard
              label="Committed to open bounties"
              value={formatAte(ledger.committed_ate)}
            />
            <SummaryCard label="Free to fund" value={formatAte(ledger.free_ate)} />
          </div>

          {ledger.exchange_error?.trim() && (
            <p className="bg-amber-50 border border-amber-200 text-amber-800 rounded-lg p-3 text-sm">
              The exchange balance could not be loaded. The bounty rows below come from
              SettleBridge only.
            </p>
          )}

          <section className="bg-white border border-gray-200 rounded-xl overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100">
              <h2 className="font-semibold text-navy-900">Bounties</h2>
            </div>
            {ledger.entries.length === 0 ? (
              <p className="px-6 py-10 text-center text-sm text-gray-500">
                No bounty activity yet.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-100">
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">
                        Role
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">
                        Bounty
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">
                        State
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">
                        Amount
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">
                        Fee
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">
                        Updated
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {ledger.entries.map((entry, index) => {
                      const issueHref = safeHttpUrl(entry.issue_url);
                      const prHref = safeHttpUrl(entry.pr_url);
                      const issueLabel = `${entry.repo}#${entry.issue_number}`;
                      return (
                        <tr
                          key={`${entry.bounty_id}:${entry.role}:${index}`}
                          className="border-b border-gray-100 last:border-0 hover:bg-gray-50"
                        >
                          <td className="px-4 py-3 capitalize text-gray-700 whitespace-nowrap">
                            {entry.role}
                          </td>
                          <td className="px-4 py-3 min-w-56">
                            {entry.title.trim() && (
                              <p className="font-medium text-navy-900">{entry.title}</p>
                            )}
                            <p className="text-xs text-gray-500 mt-0.5">
                              {issueHref ? (
                                <ExternalAnchor href={issueHref}>{issueLabel}</ExternalAnchor>
                              ) : (
                                issueLabel
                              )}
                              {prHref && (
                                <>
                                  <span className="mx-1.5 text-gray-300">·</span>
                                  <ExternalAnchor href={prHref}>
                                    {entry.pr_number != null ? `PR #${entry.pr_number}` : "PR"}
                                  </ExternalAnchor>
                                </>
                              )}
                            </p>
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            <span
                              className={`text-xs font-medium px-2.5 py-1 rounded-full capitalize ${
                                STATE_STYLES[entry.state] || "bg-gray-100 text-gray-600"
                              }`}
                            >
                              {entry.state}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-right whitespace-nowrap text-navy-900 tabular-nums">
                            {formatAte(entry.amount_ate)}
                          </td>
                          <td className="px-4 py-3 text-right whitespace-nowrap text-gray-700 tabular-nums">
                            {formatAte(entry.fee_ate)}
                          </td>
                          <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                            {formatWhen(entry.updated_at)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {ledger.other_escrows.length > 0 && (
            <section className="bg-white border border-gray-200 rounded-xl overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-100">
                <h2 className="font-semibold text-navy-900">Other holds</h2>
                <p className="text-xs text-gray-500 mt-1">
                  These are exchange escrows not tied to a GitHub bounty.
                </p>
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-100">
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">
                        Counterparty
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">
                        Status
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">
                        Held
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">
                        Review due
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {ledger.other_escrows.map((escrow) => {
                      const counterparty = counterpartyLabel(escrow);
                      return (
                        <tr
                          key={escrow.escrow_id}
                          className="border-b border-gray-100 last:border-0 hover:bg-gray-50"
                        >
                          <td className="px-4 py-3">
                            <span
                              className={
                                counterparty.isId
                                  ? "font-mono text-xs text-navy-900"
                                  : "font-medium text-navy-900"
                              }
                            >
                              {counterparty.text}
                            </span>
                          </td>
                          <td className="px-4 py-3 capitalize text-gray-700 whitespace-nowrap">
                            {escrow.status.replaceAll("_", " ") || "—"}
                          </td>
                          <td className="px-4 py-3 text-right whitespace-nowrap text-navy-900 tabular-nums">
                            {formatAte(escrow.held_ate)}
                          </td>
                          <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                            {escrow.efficacy_check_at
                              ? formatWhen(escrow.efficacy_check_at, true)
                              : ""}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
