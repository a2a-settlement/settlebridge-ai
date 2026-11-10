import { Navigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import AdminOnly from "../pages/AdminOnly";

export default function AdminRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading)
    return (
      <div className="flex items-center justify-center min-h-screen">
        Loading...
      </div>
    );
  if (!user) return <Navigate to="/login" />;
  if (!user.is_admin) return <AdminOnly />;
  return <>{children}</>;
}
