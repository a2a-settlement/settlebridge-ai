"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ClipboardList } from "lucide-react";
import api from "@/services/api";
import type { Claim } from "@/lib/types";

export default function MyClaims() {
  const [claims, setClaims] = useState<Claim[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<Claim[]>("/bounties/my/claimed")
      .then(({ data }) => setClaims(data))
      .finally(() => setLoading(false));
  }, []);

  const statusColors: Record<string, string> = {
    active: "bg-blue-100 text-blue-700",
    submitted: "bg-purple-100 text-purple-700",
    accepted: "bg-green-100 text-green-800",
    rejected: "bg-red-100 text-red-700",
    abandoned: "bg-gray-100 text-gray-500",
  };

  return (
    <div>
      <h2 className="text-lg font-semibold text-navy-900 mb-6">My Claims</h2>
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="bg-white border rounded-xl p-5 h-20 animate-pulse"
            />
          ))}
        </div>
      ) : claims.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <ClipboardList className="w-12 h-12 mx-auto mb-3 opacity-50" />
          <p className="font-medium">No claims yet</p>
          <p className="text-sm mt-1">
            <Link href="/bounties" className="text-navy-600 hover:underline">
              Browse bounties
            </Link>{" "}
            to find work
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {claims.map((c) => (
            <div
              key={c.id}
              className="bg-white border border-gray-200 rounded-xl p-4 flex items-center justify-between"
            >
              <div>
                <Link
                  href={`/bounties/${c.bounty_id}`}
                  className="font-medium text-navy-900 hover:underline text-sm"
                >
                  Bounty {c.bounty_id.slice(0, 8)}...
                </Link>
                <p className="text-xs text-gray-500 mt-0.5">
                  Claimed {new Date(c.claimed_at).toLocaleDateString()}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <span
                  className={`text-xs font-medium px-2.5 py-1 rounded-full ${statusColors[c.status] || "bg-gray-100"}`}
                >
                  {c.status}
                </span>
                {c.status === "active" && (
                  <Link
                    href={`/dashboard/claims/${c.id}/submit`}
                    className="px-3 py-1.5 bg-navy-900 text-white rounded-lg text-xs font-semibold hover:bg-navy-800 transition"
                  >
                    Submit Work
                  </Link>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
