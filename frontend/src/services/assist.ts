import type { AssistSession, Bounty, BountyDraft } from "../types";

const BASE = import.meta.env.VITE_API_URL || "/api";

function getToken(): string {
  return localStorage.getItem("sb_token") || "";
}

export interface SSECallbacks {
  onToken: (text: string) => void;
  onDraft: (draft: BountyDraft) => void;
  onStatus: (status: string) => void;
  onDone: () => void;
  onError: (err: string) => void;
}

export async function streamAssistRequest(
  url: string,
  body: Record<string, unknown>,
  callbacks: SSECallbacks,
  signal?: AbortSignal,
): Promise<string | null> {
  const response = await fetch(`${BASE}${url}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify(body),
    signal,
  });

  const sessionId = response.headers.get("X-Session-Id");

  if (!response.ok || !response.body) {
    const text = await response.text();
    callbacks.onError(text || "Stream request failed");
    return sessionId;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    let eventType = "";
    for (const line of lines) {
      if (line.startsWith("event: ")) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        const data = line.slice(6);
        try {
          switch (eventType) {
            case "token":
              callbacks.onToken(JSON.parse(data));
              break;
            case "draft":
              callbacks.onDraft(JSON.parse(data));
              break;
            case "status":
              callbacks.onStatus(JSON.parse(data));
              break;
            case "done":
              callbacks.onDone();
              break;
            case "error":
              callbacks.onError(JSON.parse(data));
              break;
          }
        } catch {
          // skip malformed events
        }
        eventType = "";
      }
    }
  }

  return sessionId;
}

export async function startSession(
  initialMessage: string,
  callbacks: SSECallbacks,
  signal?: AbortSignal,
): Promise<string | null> {
  return streamAssistRequest(
    "/assist/sessions",
    { initial_message: initialMessage },
    callbacks,
    signal,
  );
}

export async function sendMessage(
  sessionId: string,
  content: string,
  callbacks: SSECallbacks,
  signal?: AbortSignal,
): Promise<void> {
  await streamAssistRequest(
    `/assist/sessions/${sessionId}/messages`,
    { content },
    callbacks,
    signal,
  );
}

export async function getSession(sessionId: string): Promise<AssistSession> {
  const resp = await fetch(`${BASE}/assist/sessions/${sessionId}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!resp.ok) throw new Error("Failed to fetch session");
  return resp.json();
}

export async function finalizeSession(
  sessionId: string,
  overrides?: Partial<BountyDraft>,
): Promise<Bounty> {
  const body: Record<string, unknown> = {};
  if (overrides?.title) body.title = overrides.title;
  if (overrides?.description) body.description = overrides.description;
  if (overrides?.reward_suggestion) body.reward_amount = overrides.reward_suggestion;
  if (overrides?.difficulty) body.difficulty = overrides.difficulty;
  if (overrides?.provenance_tier) body.provenance_tier = overrides.provenance_tier;

  const resp = await fetch(`${BASE}/assist/sessions/${sessionId}/finalize`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: "Finalize failed" }));
    throw new Error(err.detail || "Finalize failed");
  }
  return resp.json();
}

export async function abandonSession(sessionId: string): Promise<void> {
  await fetch(`${BASE}/assist/sessions/${sessionId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${getToken()}` },
  });
}
