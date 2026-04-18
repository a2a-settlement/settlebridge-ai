import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Search, SlidersHorizontal, Sparkles } from "lucide-react";
import api from "../services/api";
import type { Bounty, BountyListResponse, TrainingCardData } from "../types";
import BountyCard from "../components/BountyCard";
import TrainingRunCard from "../components/TrainingRunCard";
import CategoryFilter from "../components/CategoryFilter";

type ViewMode = "open" | "completed";
type ResultType = "all" | "bounties" | "training";

export default function BountyFeed() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [viewMode, setViewMode] = useState<ViewMode>("open");
  const [resultType, setResultType] = useState<ResultType>("all");
  const [bounties, setBounties] = useState<Bounty[]>([]);
  const [trainingRuns, setTrainingRuns] = useState<TrainingCardData[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState(searchParams.get("search") || "");
  const [category, setCategory] = useState<string | null>(
    searchParams.get("category") || null
  );
  const [difficulty, setDifficulty] = useState(
    searchParams.get("difficulty") || ""
  );
  const [showFilters, setShowFilters] = useState(false);

  const page = parseInt(searchParams.get("page") || "1");

  // Fetch open bounties
  useEffect(() => {
    if (viewMode !== "open") return;
    setLoading(true);
    const params = new URLSearchParams();
    params.set("status", "open");
    params.set("page", String(page));
    params.set("page_size", "12");
    if (search) params.set("search", search);
    if (category) params.set("category_id", category);
    if (difficulty) params.set("difficulty", difficulty);

    api
      .get<BountyListResponse>(`/bounties?${params}`)
      .then(({ data }) => {
        setBounties(data.bounties);
        setTotal(data.total);
      })
      .finally(() => setLoading(false));
  }, [viewMode, page, search, category, difficulty]);

  // Fetch completed results (bounties + training runs)
  useEffect(() => {
    if (viewMode !== "completed") return;
    setLoading(true);

    const fetchBounties =
      resultType !== "training"
        ? api
            .get<BountyListResponse>(
              `/bounties?status=completed&page=1&page_size=20`
            )
            .then(({ data }) => data.bounties)
            .catch(() => [] as Bounty[])
        : Promise.resolve([] as Bounty[]);

    const fetchTraining =
      resultType !== "bounties"
        ? api
            .get<TrainingCardData[]>("/training/public?limit=20")
            .then(({ data }) => data)
            .catch(() => [] as TrainingCardData[])
        : Promise.resolve([] as TrainingCardData[]);

    Promise.all([fetchBounties, fetchTraining])
      .then(([b, t]) => {
        // Deduplicate: if a training run already covers a bounty, don't show
        // that bounty as a plain card too (avoids double-listing).
        const coveredBountyIds = new Set(t.map((r) => r.bounty_id));
        const dedupedBounties = b.filter((bty) => !coveredBountyIds.has(bty.id));
        setBounties(dedupedBounties);
        setTrainingRuns(t);
        setTotal(dedupedBounties.length + t.length);
      })
      .finally(() => setLoading(false));
  }, [viewMode, resultType]);

  const totalPages = viewMode === "open" ? Math.ceil(total / 12) : 1;

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-navy-900">Bounties</h1>
          <p className="text-gray-500 text-sm mt-1">
            {total} {viewMode === "open" ? "bounties available" : "results"}
          </p>
        </div>
        <Link
          to="/bounties/new"
          className="px-5 py-2.5 bg-money text-navy-900 rounded-lg font-semibold text-sm hover:bg-money-dark transition"
        >
          Post Bounty
        </Link>
      </div>

      {/* Bounty Assist Banner */}
      <Link
        to="/bounties/assist"
        className="flex items-center gap-3 p-3.5 mb-6 bg-navy-900 rounded-xl hover:bg-navy-800 transition group"
      >
        <div className="w-8 h-8 bg-money/20 rounded-lg flex items-center justify-center flex-shrink-0">
          <Sparkles className="w-4 h-4 text-money" />
        </div>
        <p className="text-sm text-gray-300 flex-1">
          <span className="text-white font-medium">Need help crafting your bounty?</span>{" "}
          Let AI turn your question into a structured intelligence requirement.
        </p>
        <span className="text-xs font-medium text-money group-hover:underline flex-shrink-0">
          Bounty Assist &rarr;
        </span>
      </Link>

      {/* View Mode Tabs */}
      <div className="flex gap-1 mb-5 bg-gray-100 rounded-lg p-1 w-fit">
        <button
          onClick={() => setViewMode("open")}
          className={`px-4 py-1.5 rounded-md text-sm font-medium transition ${
            viewMode === "open"
              ? "bg-white text-navy-900 shadow-sm"
              : "text-gray-500 hover:text-gray-700"
          }`}
        >
          Open Bounties
        </button>
        <button
          onClick={() => setViewMode("completed")}
          className={`px-4 py-1.5 rounded-md text-sm font-medium transition ${
            viewMode === "completed"
              ? "bg-white text-navy-900 shadow-sm"
              : "text-gray-500 hover:text-gray-700"
          }`}
        >
          Completed Results
        </button>
      </div>

      {/* Search + Filter Bar */}
      <div className="flex gap-3 mb-6">
        {viewMode === "open" && (
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder="Search bounties..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-10 pr-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
            />
          </div>
        )}
        {viewMode === "open" && (
          <select
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value)}
            className="px-4 py-2.5 rounded-lg border border-gray-300 text-sm focus:ring-2 focus:ring-navy-500 outline-none"
          >
            <option value="">All Difficulties</option>
            <option value="trivial">Trivial</option>
            <option value="easy">Easy</option>
            <option value="medium">Medium</option>
            <option value="hard">Hard</option>
            <option value="expert">Expert</option>
          </select>
        )}
        {viewMode === "completed" && (
          <select
            value={resultType}
            onChange={(e) => setResultType(e.target.value as ResultType)}
            className="px-4 py-2.5 rounded-lg border border-gray-300 text-sm focus:ring-2 focus:ring-navy-500 outline-none"
          >
            <option value="all">All Results</option>
            <option value="bounties">Bounties Only</option>
            <option value="training">Training Runs</option>
          </select>
        )}
        <button
          onClick={() => setShowFilters(!showFilters)}
          className="lg:hidden px-3 py-2.5 rounded-lg border border-gray-300 text-gray-600 hover:bg-gray-50"
        >
          <SlidersHorizontal className="w-4 h-4" />
        </button>
      </div>

      <div className="flex gap-8">
        {/* Sidebar (open mode only) */}
        {viewMode === "open" && (
          <aside
            className={`w-56 flex-shrink-0 ${showFilters ? "block" : "hidden"} lg:block`}
          >
            <h3 className="font-semibold text-navy-900 text-sm mb-3">
              Categories
            </h3>
            <CategoryFilter selected={category} onChange={setCategory} />
          </aside>
        )}

        {/* Grid */}
        <div className="flex-1">
          {loading ? (
            <div className="grid md:grid-cols-2 gap-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <div
                  key={i}
                  className="bg-white border border-gray-200 rounded-xl p-5 animate-pulse h-44"
                />
              ))}
            </div>
          ) : viewMode === "open" ? (
            bounties.length === 0 ? (
              <div className="text-center py-20 text-gray-400">
                <p className="text-lg font-medium mb-2">No bounties found</p>
                <p className="text-sm">Try adjusting your filters</p>
              </div>
            ) : (
              <>
                <div className="grid md:grid-cols-2 gap-4">
                  {bounties.map((b) => (
                    <BountyCard key={b.id} bounty={b} />
                  ))}
                </div>
                {totalPages > 1 && (
                  <div className="flex justify-center gap-2 mt-8">
                    {Array.from({ length: totalPages }, (_, i) => (
                      <button
                        key={i}
                        onClick={() => {
                          const p = new URLSearchParams(searchParams);
                          p.set("page", String(i + 1));
                          setSearchParams(p);
                        }}
                        className={`w-9 h-9 rounded-lg text-sm font-medium transition ${
                          page === i + 1
                            ? "bg-navy-900 text-white"
                            : "bg-white border border-gray-300 text-gray-600 hover:bg-gray-50"
                        }`}
                      >
                        {i + 1}
                      </button>
                    ))}
                  </div>
                )}
              </>
            )
          ) : /* completed mode */ bounties.length === 0 &&
            trainingRuns.length === 0 ? (
            <div className="text-center py-20 text-gray-400">
              <p className="text-lg font-medium mb-2">No results yet</p>
              <p className="text-sm">
                {resultType === "training"
                  ? "No public training runs found"
                  : "No completed bounties found"}
              </p>
            </div>
          ) : (
            <div className="grid md:grid-cols-2 gap-4">
              {/* Merge and sort by date descending so newest items appear first */}
              {[
                ...trainingRuns.map((r) => ({ type: "training" as const, date: r.completed_at ?? r.created_at, item: r })),
                ...bounties.map((b) => ({ type: "bounty" as const, date: b.completed_at ?? b.created_at, item: b })),
              ]
                .sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime())
                .map((entry) =>
                  entry.type === "training" ? (
                    <TrainingRunCard key={entry.item.run_id} run={entry.item} />
                  ) : (
                    <BountyCard key={entry.item.id} bounty={entry.item} />
                  )
                )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
