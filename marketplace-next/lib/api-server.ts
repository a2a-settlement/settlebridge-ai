import "server-only";
import type {
  Bounty,
  BountyListResponse,
  Category,
  ContractListResponse,
  ExchangeAgentSummary,
  ServiceContract,
  SnapshotListResponse,
  TrainingCardData,
} from "@/lib/types";

function internalBase(): string {
  return (process.env.INTERNAL_API_BASE_URL ?? "http://127.0.0.1:8002/api").replace(
    /\/$/,
    ""
  );
}

async function apiGet<T>(
  path: string,
  init?: RequestInit & { next?: { revalidate?: number; tags?: string[] } }
): Promise<T> {
  const url = `${internalBase()}${path.startsWith("/") ? path : `/${path}`}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers as Record<string, string>),
    },
  });
  if (!res.ok) {
    throw new Error(`GET ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function fetchBountyList(
  query: Record<string, string>,
  revalidateSeconds: number
): Promise<BountyListResponse> {
  const q = new URLSearchParams(query);
  return apiGet<BountyListResponse>(`/bounties?${q}`, {
    next: { revalidate: revalidateSeconds },
  });
}

export async function fetchBountyById(id: string): Promise<Bounty> {
  return apiGet<Bounty>(`/bounties/${id}`, { cache: "no-store" });
}

export async function fetchBountySubmissions(
  bountyId: string
): Promise<unknown[]> {
  return apiGet<unknown[]>(`/bounties/${bountyId}/submissions`, {
    cache: "no-store",
  });
}

export async function fetchCategories(): Promise<Category[]> {
  return apiGet<Category[]>("/categories", { next: { revalidate: 300 } });
}

export async function fetchAgentsList(): Promise<ExchangeAgentSummary[]> {
  const data = await apiGet<{ agents: ExchangeAgentSummary[] }>("/agents", {
    next: { revalidate: 300 },
  });
  return data.agents ?? [];
}

export async function fetchAgentById(
  botId: string
): Promise<ExchangeAgentSummary | null> {
  try {
    return await apiGet<ExchangeAgentSummary>(`/agents/${botId}`, {
      next: { revalidate: 120 },
    });
  } catch {
    return null;
  }
}

export async function fetchContractsList(
  status?: string
): Promise<ContractListResponse> {
  const q = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiGet<ContractListResponse>(`/contracts${q}`, {
    next: { revalidate: 60 },
  });
}

export async function fetchContractById(id: string): Promise<ServiceContract> {
  return apiGet<ServiceContract>(`/contracts/${id}`, {
    next: { revalidate: 60 },
  });
}

export async function fetchContractSnapshots(
  id: string
): Promise<SnapshotListResponse> {
  return apiGet<SnapshotListResponse>(`/contracts/${id}/snapshots`, {
    next: { revalidate: 60 },
  });
}

export async function fetchTrainingPublic(
  limit = 20
): Promise<TrainingCardData[]> {
  return apiGet<TrainingCardData[]>(`/training/public?limit=${limit}`, {
    next: { revalidate: 60 },
  });
}

/** For sitemap: paginate open + completed bounties (page_size max 100 on API). */
export async function fetchBountyIdsForSitemap(): Promise<
  { id: string; updated_at?: string }[]
> {
  const out: { id: string; updated_at?: string }[] = [];
  for (const status of ["open", "completed"]) {
    let page = 1;
    try {
      while (page <= 20) {
        const data = await apiGet<BountyListResponse>(
          `/bounties?status=${status}&page=${page}&page_size=100`,
          { next: { revalidate: 3600 } }
        );
        for (const b of data.bounties) {
          out.push({ id: b.id, updated_at: b.updated_at });
        }
        if (data.bounties.length < 100 || page * 100 >= data.total) break;
        page += 1;
      }
    } catch {
      /* ignore */
    }
  }
  return out;
}

export async function fetchContractIdsForSitemap(): Promise<
  { id: string; updated_at?: string }[]
> {
  try {
    const out: { id: string; updated_at?: string }[] = [];
    let offset = 0;
    while (offset < 2000) {
      const data = await apiGet<ContractListResponse>(
        `/contracts?limit=100&offset=${offset}`,
        { next: { revalidate: 3600 } }
      );
      for (const c of data.contracts) {
        out.push({ id: c.id, updated_at: c.updated_at });
      }
      if (data.contracts.length < 100 || out.length >= data.total) break;
      offset += 100;
    }
    return out;
  } catch {
    return [];
  }
}
