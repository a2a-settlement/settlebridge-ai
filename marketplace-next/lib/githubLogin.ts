import api from "@/services/api";
import { safeReturnPath } from "@/lib/returnTo";

const NONCE_KEY = "sb_github_login_nonce";

/** Send the browser to GitHub to sign in, coming back to `next` afterwards. */
export async function startGitHubLogin(next: string | null): Promise<void> {
  const { data } = await api.post<{ authorize_url: string; nonce: string }>(
    "/auth/github/start",
    { next: safeReturnPath(next) }
  );
  sessionStorage.setItem(NONCE_KEY, data.nonce);
  window.location.assign(data.authorize_url);
}

export function takeGitHubLoginNonce(): string | null {
  const nonce = sessionStorage.getItem(NONCE_KEY);
  sessionStorage.removeItem(NONCE_KEY);
  return nonce;
}

/**
 * Whether an OAuth `state` came from the sign-in flow rather than account linking.
 * Only picks which endpoint to call; the server verifies the state.
 */
export function isGitHubLoginState(state: string | null): boolean {
  const payload = state?.split(".")[1];
  if (!payload) return false;
  try {
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json)?.purpose === "github_login";
  } catch {
    return false;
  }
}
