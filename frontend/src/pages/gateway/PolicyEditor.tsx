import { useCallback, useState } from "react";
import { Plus, Trash2, CheckCircle, XCircle } from "lucide-react";
import YamlEditor from "../../components/gateway/YamlEditor";
import {
  fetchPolicies,
  createPolicy,
  deletePolicy,
  validatePolicy,
  usePolling,
} from "../../services/gateway";
import type { PolicyValidationResult, TrustPolicy } from "../../types/gateway";

const TEMPLATE = `version: "1"
policies:
  - name: my-policy
    match: { all_agents: true }
    rules:
      - reputation_gte: 0.3
`;

export default function PolicyEditor() {
  const { data: policies, refresh } = usePolling(useCallback(() => fetchPolicies(), []), 15000);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [yaml, setYaml] = useState(TEMPLATE);
  const [validation, setValidation] = useState<PolicyValidationResult | null>(null);
  const [saving, setSaving] = useState(false);

  const handleValidate = async () => {
    const result = await validatePolicy(name || "test", yaml);
    setValidation(result);
  };

  const handleSave = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      await createPolicy(name, yaml);
      setEditing(false);
      setName("");
      setYaml(TEMPLATE);
      setValidation(null);
      refresh();
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    await deletePolicy(id);
    refresh();
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Trust Policies</h1>
          <p className="text-sm text-gray-500 mt-1">
            Define rules that govern agent interactions through the gateway
          </p>
        </div>
        {!editing && (
          <button
            onClick={() => setEditing(true)}
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition"
          >
            <Plus className="w-4 h-4" /> New Policy
          </button>
        )}
      </div>

      {editing && (
        <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
          <input
            type="text"
            placeholder="Policy name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          />
          <YamlEditor value={yaml} onChange={setYaml} />

          {validation && (
            <div className={`p-3 rounded-lg text-sm ${validation.valid ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"}`}>
              {validation.valid ? (
                <div className="flex items-center gap-2">
                  <CheckCircle className="w-4 h-4" />
                  <span>
                    Valid. Would match {validation.matched_transactions} transactions,
                    block {validation.would_block}.
                  </span>
                </div>
              ) : (
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <XCircle className="w-4 h-4" />
                    <span>Validation errors:</span>
                  </div>
                  <ul className="list-disc list-inside">
                    {validation.errors.map((e, i) => <li key={i}>{e}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}

          <div className="flex items-center gap-3">
            <button
              onClick={handleValidate}
              className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 transition"
            >
              Test Policy
            </button>
            <button
              onClick={handleSave}
              disabled={saving || !name.trim()}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition"
            >
              {saving ? "Saving..." : "Save Policy"}
            </button>
            <button
              onClick={() => { setEditing(false); setValidation(null); }}
              className="px-4 py-2 text-gray-500 text-sm hover:text-gray-700"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="space-y-3">
        {policies?.map((policy: TrustPolicy) => (
          <div key={policy.id} className="bg-white rounded-xl border border-gray-200 p-5">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="font-medium text-gray-900">{policy.name}</h3>
                <p className="text-xs text-gray-400">
                  v{policy.version} · Updated {new Date(policy.updated_at).toLocaleDateString()}
                </p>
              </div>
              <button
                onClick={() => handleDelete(policy.id)}
                className="text-gray-400 hover:text-red-600 transition"
                title="Deactivate"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
            <YamlEditor value={policy.yaml_content} onChange={() => {}} readOnly height="8rem" />
          </div>
        ))}
        {policies?.length === 0 && (
          <p className="text-center text-gray-400 py-8">No active policies</p>
        )}
      </div>
    </div>
  );
}
