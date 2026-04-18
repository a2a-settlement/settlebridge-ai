import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "/api",
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("sb_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Only redirect to login when a token was actually sent (i.e. the user
      // believed they were logged in).  Unauthenticated requests to protected
      // endpoints should fail silently rather than bouncing the user to /login.
      const hadToken = !!localStorage.getItem("sb_token");
      localStorage.removeItem("sb_token");
      if (hadToken) {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

export default api;
