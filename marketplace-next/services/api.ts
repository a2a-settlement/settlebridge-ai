import axios from "axios";
import type { CyberTaskListResponse, CyberTaskView } from "@/lib/types";

const api = axios.create({
  baseURL: "/api",
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  if (typeof window === "undefined") return config;
  const token = localStorage.getItem("sb_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (typeof window === "undefined") return Promise.reject(error);
    if (error.response?.status === 401) {
      const hadToken = !!localStorage.getItem("sb_token");
      localStorage.removeItem("sb_token");
      if (hadToken) {
        const next = `${window.location.pathname}${window.location.search}`;
        window.location.href = `/login?next=${encodeURIComponent(next)}`;
      }
    }
    return Promise.reject(error);
  }
);

export async function fetchCyberTasks(query: {
  view: CyberTaskView;
  page?: number;
  page_size?: number;
}): Promise<CyberTaskListResponse> {
  const { data } = await api.get<CyberTaskListResponse>("/v1/cyber/tasks", {
    params: {
      view: query.view,
      page: query.page ?? 1,
      page_size: query.page_size ?? 12,
    },
  });
  return data;
}

export default api;
