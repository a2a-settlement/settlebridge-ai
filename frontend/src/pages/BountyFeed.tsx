import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Search, SlidersHorizontal } from "lucide-react";
import api from "../services/api";
import type { Bounty, BountyListResponse } from "../types";
import BountyCard from "../components/BountyCard";
import CategoryFilter from "../components/CategoryFilter";

export default function BountyFeed() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [bounties, setBounties] = useState<Bounty[]>([]);
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

  useEffect(() => {
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
  }, [page, search, category, difficulty]);

  const totalPages = Math.ceil(total / 12);

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-navy-900">Bounties</h1>
          <p className="text-gray-500 text-sm mt-1">
            {total} bounties available
          </p>
        </div>
        <Link
          to="/bounties/new"
          className="px-5 py-2.5 bg-money text-navy-900 rounded-lg font-semibold text-sm hover:bg-money-dark transition"
        >
          Post Bounty
        </Link>
      </div>

      {/* Search + Filter Bar */}
      <div className="flex gap-3 mb-6">
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
        <button
          onClick={() => setShowFilters(!showFilters)}
          className="lg:hidden px-3 py-2.5 rounded-lg border border-gray-300 text-gray-600 hover:bg-gray-50"
        >
          <SlidersHorizontal className="w-4 h-4" />
        </button>
      </div>

      <div className="flex gap-8">
        {/* Sidebar */}
        <aside
          className={`w-56 flex-shrink-0 ${showFilters ? "block" : "hidden"} lg:block`}
        >
          <h3 className="font-semibold text-navy-900 text-sm mb-3">
            Categories
          </h3>
          <CategoryFilter selected={category} onChange={setCategory} />
        </aside>

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
          ) : bounties.length === 0 ? (
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
          )}
        </div>
      </div>
    </div>
  );
}
