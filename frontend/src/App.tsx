import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "./hooks/useAuth";
import Layout from "./components/Layout";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Register from "./pages/Register";
import BountyFeed from "./pages/BountyFeed";
import BountyDetail from "./pages/BountyDetail";
import PostBounty from "./pages/PostBounty";
import Dashboard from "./pages/Dashboard";
import ReviewSubmission from "./pages/ReviewSubmission";
import SubmitWork from "./pages/SubmitWork";
import AgentDirectory from "./pages/AgentDirectory";
import AgentProfile from "./pages/AgentProfile";
import Settings from "./pages/Settings";
import Demo from "./pages/Demo";
import Developers from "./pages/Developers";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading)
    return (
      <div className="flex items-center justify-center min-h-screen">
        Loading...
      </div>
    );
  if (!user) return <Navigate to="/login" />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />

      <Route element={<Layout />}>
        <Route path="/bounties" element={<BountyFeed />} />
        <Route path="/bounties/:id" element={<BountyDetail />} />
        <Route path="/agents" element={<AgentDirectory />} />
        <Route path="/agents/:botId" element={<AgentProfile />} />
        <Route path="/demo" element={<Demo />} />
        <Route path="/developers" element={<Developers />} />

        <Route
          path="/bounties/new"
          element={
            <ProtectedRoute>
              <PostBounty />
            </ProtectedRoute>
          }
        />
        <Route
          path="/dashboard/*"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />
        <Route
          path="/dashboard/submissions/:id"
          element={
            <ProtectedRoute>
              <ReviewSubmission />
            </ProtectedRoute>
          }
        />
        <Route
          path="/dashboard/claims/:id/submit"
          element={
            <ProtectedRoute>
              <SubmitWork />
            </ProtectedRoute>
          }
        />
        <Route
          path="/dashboard/settings"
          element={
            <ProtectedRoute>
              <Settings />
            </ProtectedRoute>
          }
        />
      </Route>
    </Routes>
  );
}
