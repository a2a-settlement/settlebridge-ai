import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "./hooks/useAuth";
import Layout from "./components/Layout";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Register from "./pages/Register";

// Gateway pages (primary)
import Overview from "./pages/gateway/Overview";
import AgentHealth from "./pages/gateway/AgentHealth";
import AgentDetail from "./pages/gateway/AgentDetail";
import TrustScores from "./pages/gateway/TrustScores";
import TransactionFlow from "./pages/gateway/TransactionFlow";
import AuditLog from "./pages/gateway/AuditLog";
import PolicyEditor from "./pages/gateway/PolicyEditor";
import SettlementOverview from "./pages/gateway/SettlementOverview";
import Alerts from "./pages/gateway/Alerts";
import GatewayAssist from "./pages/GatewayAssist";

// Marketplace pages (secondary)
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
import ContractList from "./pages/ContractList";
import ContractDetail from "./pages/ContractDetail";
import CreateContract from "./pages/CreateContract";
import BountyAssist from "./pages/BountyAssist";

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
      <Route path="/landing" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />

      <Route element={<Layout />}>
        {/* Gateway (primary) */}
        <Route path="/" element={<Overview />} />
        <Route path="/agents" element={<AgentHealth />} />
        <Route path="/agents/:id" element={<AgentDetail />} />
        <Route path="/trust" element={<TrustScores />} />
        <Route path="/transactions" element={<TransactionFlow />} />
        <Route path="/audit" element={<AuditLog />} />
        <Route path="/policies" element={<PolicyEditor />} />
        <Route path="/settlement" element={<SettlementOverview />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route
          path="/assist"
          element={
            <ProtectedRoute>
              <GatewayAssist />
            </ProtectedRoute>
          }
        />

        {/* Marketplace (secondary) */}
        <Route path="/marketplace" element={<BountyFeed />} />
        <Route path="/marketplace/bounties" element={<BountyFeed />} />
        <Route path="/marketplace/bounties/:id" element={<BountyDetail />} />
        <Route path="/marketplace/agents" element={<AgentDirectory />} />
        <Route path="/marketplace/agents/:botId" element={<AgentProfile />} />
        <Route path="/marketplace/contracts" element={<ContractList />} />
        <Route path="/marketplace/contracts/:id" element={<ContractDetail />} />
        <Route path="/marketplace/demo" element={<Demo />} />
        <Route path="/marketplace/developers" element={<Developers />} />
        <Route
          path="/marketplace/contracts/new"
          element={
            <ProtectedRoute>
              <CreateContract />
            </ProtectedRoute>
          }
        />
        <Route
          path="/marketplace/bounties/assist"
          element={
            <ProtectedRoute>
              <BountyAssist />
            </ProtectedRoute>
          }
        />
        <Route
          path="/marketplace/bounties/new"
          element={
            <ProtectedRoute>
              <PostBounty />
            </ProtectedRoute>
          }
        />
        <Route
          path="/marketplace/dashboard/*"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />
        <Route
          path="/marketplace/dashboard/submissions/:id"
          element={
            <ProtectedRoute>
              <ReviewSubmission />
            </ProtectedRoute>
          }
        />
        <Route
          path="/marketplace/dashboard/claims/:id/submit"
          element={
            <ProtectedRoute>
              <SubmitWork />
            </ProtectedRoute>
          }
        />
        <Route
          path="/marketplace/dashboard/settings"
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
