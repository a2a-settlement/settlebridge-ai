import { Shield } from "lucide-react";

export interface TrustDiscountPolicy {
  algorithm_id: string;
  initial_rho: number;
  parameters?: Record<string, unknown>;
}

interface Props {
  policy: TrustDiscountPolicy;
}

function formatAlgorithmId(id: string): string {
  const parts = id.split(":");
  return parts[parts.length - 1] || id;
}

export default function ExchangePolicyCard({ policy }: Props) {
  const params = policy.parameters ?? {};
  const maxRho = params.max_rho ?? params.rho_at_threshold;

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-navy-100">
          <Shield className="h-5 w-5 text-navy-700" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-navy-900">Trust Discount Policy</h3>
          <dl className="mt-2 space-y-1 text-sm">
            <div>
              <dt className="inline font-medium text-gray-500">Algorithm: </dt>
              <dd className="inline text-navy-800">
                {formatAlgorithmId(policy.algorithm_id)}
              </dd>
            </div>
            <div>
              <dt className="inline font-medium text-gray-500">Initial ρ: </dt>
              <dd className="inline text-navy-800">{policy.initial_rho}</dd>
            </div>
            {maxRho != null && (
              <div>
                <dt className="inline font-medium text-gray-500">Max ρ: </dt>
                <dd className="inline text-navy-800">{String(maxRho)}</dd>
              </div>
            )}
            {Object.keys(params).length > 0 && (
              <div>
                <dt className="font-medium text-gray-500">Parameters</dt>
                <dd className="mt-1 rounded bg-gray-50 px-2 py-1 font-mono text-xs text-navy-700">
                  {JSON.stringify(params)}
                </dd>
              </div>
            )}
          </dl>
        </div>
      </div>
    </div>
  );
}
