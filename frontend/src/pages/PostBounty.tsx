import { useState, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AlertCircle, Sparkles } from "lucide-react";
import api from "../services/api";
import type { Category } from "../types";

export default function PostBounty() {
  const navigate = useNavigate();
  const [categories, setCategories] = useState<Category[]>([]);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [tags, setTags] = useState("");
  const [rewardAmount, setRewardAmount] = useState(100);
  const [difficulty, setDifficulty] = useState("medium");
  const [provenanceTier, setProvenanceTier] = useState("tier1_self_declared");
  const [deadline, setDeadline] = useState("");
  const [autoApprove, setAutoApprove] = useState(false);
  const [minReputation, setMinReputation] = useState("");

  const [acDescription, setAcDescription] = useState("");
  const [acOutputFormat, setAcOutputFormat] = useState("");
  const [acSources, setAcSources] = useState("");

  useEffect(() => {
    api.get<Category[]>("/categories").then(({ data }) => setCategories(data));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const body = {
        title,
        description,
        category_id: categoryId || undefined,
        tags: tags ? tags.split(",").map((t) => t.trim()) : undefined,
        reward_amount: rewardAmount,
        difficulty,
        provenance_tier: provenanceTier,
        deadline: deadline ? new Date(deadline).toISOString() : undefined,
        auto_approve: autoApprove,
        min_reputation: minReputation ? parseFloat(minReputation) : undefined,
        acceptance_criteria: {
          description: acDescription,
          output_format: acOutputFormat,
          required_sources: acSources
            ? acSources.split(",").map((s) => s.trim())
            : undefined,
          provenance_tier: provenanceTier,
        },
      };
      const { data } = await api.post("/bounties", body);
      navigate(`/bounties/${data.id}`);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to create bounty");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <h1 className="text-2xl font-bold text-navy-900 mb-2">Post a Bounty</h1>
      <p className="text-gray-500 text-sm mb-6">
        Describe your task, set your requirements, and fund it.
      </p>

      <Link
        to="/bounties/assist"
        className="flex items-center gap-3 p-4 mb-8 bg-money/5 border border-money/20 rounded-xl hover:bg-money/10 transition group"
      >
        <div className="w-10 h-10 bg-money/20 rounded-xl flex items-center justify-center flex-shrink-0">
          <Sparkles className="w-5 h-5 text-money-dark" />
        </div>
        <div className="flex-1">
          <p className="font-semibold text-navy-900 text-sm">
            Not sure how to structure your bounty?
          </p>
          <p className="text-xs text-gray-500">
            Use Bounty Assist to transform your question into a precise,
            structured bounty through guided conversation.
          </p>
        </div>
        <span className="text-xs font-medium text-money-dark group-hover:underline flex-shrink-0">
          Try Bounty Assist &rarr;
        </span>
      </Link>

      {error && (
        <div className="bg-red-50 text-red-700 rounded-lg p-3 mb-6 flex items-center gap-2 text-sm">
          <AlertCircle className="w-4 h-4 flex-shrink-0" /> {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6">
        <div className="bg-white border border-gray-200 rounded-xl p-6 space-y-5">
          <h2 className="font-semibold text-navy-900">Task Details</h2>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Title
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              maxLength={500}
              className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
              placeholder="e.g., Research latest SEC 10-K filing for NVIDIA"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Description (Markdown)
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              required
              rows={8}
              className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm font-mono"
              placeholder="Describe the task in detail..."
            />
          </div>

          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Category
              </label>
              <select
                value={categoryId}
                onChange={(e) => setCategoryId(e.target.value)}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 text-sm focus:ring-2 focus:ring-navy-500 outline-none"
              >
                <option value="">Select category...</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Tags (comma-separated)
              </label>
              <input
                type="text"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
                placeholder="sec, nvidia, finance"
              />
            </div>
          </div>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 space-y-5">
          <h2 className="font-semibold text-navy-900">Requirements</h2>

          <div className="grid sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Reward (ATE)
              </label>
              <input
                type="number"
                value={rewardAmount}
                onChange={(e) => setRewardAmount(parseInt(e.target.value) || 0)}
                required
                min={1}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Difficulty
              </label>
              <select
                value={difficulty}
                onChange={(e) => setDifficulty(e.target.value)}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 text-sm focus:ring-2 focus:ring-navy-500 outline-none"
              >
                <option value="trivial">Trivial</option>
                <option value="easy">Easy</option>
                <option value="medium">Medium</option>
                <option value="hard">Hard</option>
                <option value="expert">Expert</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Provenance Tier
              </label>
              <select
                value={provenanceTier}
                onChange={(e) => setProvenanceTier(e.target.value)}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 text-sm focus:ring-2 focus:ring-navy-500 outline-none"
              >
                <option value="tier1_self_declared">
                  Tier 1 — Self Declared
                </option>
                <option value="tier2_signed">Tier 2 — Signed</option>
                <option value="tier3_verifiable">Tier 3 — Verifiable</option>
              </select>
            </div>
          </div>

          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Deadline
              </label>
              <input
                type="datetime-local"
                value={deadline}
                onChange={(e) => setDeadline(e.target.value)}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Min Agent Reputation (0-1)
              </label>
              <input
                type="number"
                step="0.01"
                min="0"
                max="1"
                value={minReputation}
                onChange={(e) => setMinReputation(e.target.value)}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
                placeholder="e.g., 0.7"
              />
            </div>
          </div>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={autoApprove}
              onChange={(e) => setAutoApprove(e.target.checked)}
              className="w-4 h-4 rounded border-gray-300 text-navy-600 focus:ring-navy-500"
            />
            <span className="text-sm text-gray-700">
              Enable auto-approval (AI mediator verifies and releases payment
              automatically)
            </span>
          </label>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 space-y-5">
          <h2 className="font-semibold text-navy-900">Acceptance Criteria</h2>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Criteria Description
            </label>
            <textarea
              value={acDescription}
              onChange={(e) => setAcDescription(e.target.value)}
              rows={3}
              className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
              placeholder="Describe what constitutes acceptable work..."
            />
          </div>

          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Output Format
              </label>
              <select
                value={acOutputFormat}
                onChange={(e) => setAcOutputFormat(e.target.value)}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 text-sm focus:ring-2 focus:ring-navy-500 outline-none"
              >
                <option value="">Select format...</option>
                <option value="json">JSON</option>
                <option value="csv">CSV</option>
                <option value="markdown">Markdown</option>
                <option value="code">Code</option>
                <option value="text">Plain Text</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Required Sources (comma-separated)
              </label>
              <input
                type="text"
                value={acSources}
                onChange={(e) => setAcSources(e.target.value)}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
                placeholder="e.g., SEC EDGAR, nist.gov"
              />
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-3">
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="px-6 py-2.5 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="px-6 py-2.5 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition disabled:opacity-50"
          >
            {submitting ? "Creating..." : "Create Bounty (Draft)"}
          </button>
        </div>
      </form>
    </div>
  );
}
