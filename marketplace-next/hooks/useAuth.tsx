import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react";
import api from "@/services/api";
import type { User } from "@/lib/types";

interface AuthContextType {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  exchangeLogin: (apiKey: string) => Promise<void>;
  githubLogin: (code: string, state: string, nonce: string) => Promise<GitHubLoginResult>;
  register: (
    email: string,
    password: string,
    displayName: string,
    userType: string
  ) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
}

export interface GitHubLoginResult {
  login: string;
  created: boolean;
  next: string | null;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const token = localStorage.getItem("sb_token");
    if (!token) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const { data } = await api.get<User>("/auth/me");
      setUser(data);
    } catch {
      localStorage.removeItem("sb_token");
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = async (email: string, password: string) => {
    const { data } = await api.post<{ access_token: string }>("/auth/login", {
      email,
      password,
    });
    localStorage.setItem("sb_token", data.access_token);
    await refresh();
  };

  const exchangeLogin = async (apiKey: string) => {
    const { data } = await api.post<{ access_token: string }>(
      "/auth/exchange-login",
      { api_key: apiKey }
    );
    localStorage.setItem("sb_token", data.access_token);
    await refresh();
  };

  const githubLogin = async (code: string, state: string, nonce: string) => {
    const { data } = await api.post<GitHubLoginResult & { access_token: string }>(
      "/auth/github/callback",
      { code, state, nonce }
    );
    localStorage.setItem("sb_token", data.access_token);
    await refresh();
    return { login: data.login, created: data.created, next: data.next };
  };

  const register = async (
    email: string,
    password: string,
    displayName: string,
    userType: string
  ) => {
    const { data } = await api.post<{ access_token: string }>(
      "/auth/register",
      {
        email,
        password,
        display_name: displayName,
        user_type: userType,
      }
    );
    localStorage.setItem("sb_token", data.access_token);
    await refresh();
  };

  const logout = () => {
    localStorage.removeItem("sb_token");
    setUser(null);
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        login,
        exchangeLogin,
        githubLogin,
        register,
        logout,
        refresh,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
