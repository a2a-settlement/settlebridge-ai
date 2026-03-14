import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "./hooks/useAuth";
import { AppConfigContext, useAppConfig, useAppConfigLoader } from "./hooks/useAppConfig";
import Layout from "./components/Layout";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Register from "./pages/Register";

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

const BountyFeed = lazy(() => import("./pages/BountyFeed"));
const BountyDetail = lazy(() => import("./pages/BountyDetail"));
const PostBounty = lazy(() => import("./pages/PostBounty"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const ReviewSubmission = lazy(() => import("./pages/ReviewSubmission"));
const SubmitWork = lazy(() => import("./pages/SubmitWork"));
const AgentDirectory = lazy(() => import("./pages/AgentDirectory"));
const AgentProfile = lazy(() => import("./pages/AgentProfile"));
const Settings = lazy(() => import("./pages/Settings"));
const Demo = lazy(() => import("./pages/Demo"));
const Developers = lazy(() => import("./pages/Developers"));
const ContractList = lazy(() => import("./pages/ContractList"));
const ContractDetail = lazy(() => import("./pages/ContractDetail"));
const CreateContract = lazy(() => import("./pages/CreateContract"));
const BountyAssist = lazy(() => import("./pages/BountyAssist"));

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

function MarketplaceRoutes() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center py-20 text-gray-400">Loading...</div>}>
      <Routes>
        <Route path="/" element={<BountyFeed />} />
        <Route path="/bounties" element={<BountyFeed />} />
        <Route path="/bounties/:id" element={<BountyDetail />} />
        <Route path="/agents" element={<AgentDirectory />} />
        <Route path="/agents/:botId" element={<AgentProfile />} />
        <Route path="/contracts" element={<ContractList />} />
        <Route path="/contracts/:id" element={<ContractDetail />} />
        <Route path="/demo" element={<Demo />} />
        <Route path="/developers" element={<Developers />} />
        <Route
          path="/contracts/new"
          element={<ProtectedRoute><CreateContract /></ProtectedRoute>}
        />
        <Route
          path="/bounties/assist"
          element={<ProtectedRoute><BountyAssist /></ProtectedRoute>}
        />
        <Route
          path="/bounties/new"
          element={<ProtectedRoute><PostBounty /></ProtectedRoute>}
        />
        <Route
          path="/dashboard/*"
          element={<ProtectedRoute><Dashboard /></ProtectedRoute>}
        />
        <Route
          path="/dashboard/submissions/:id"
          element={<ProtectedRoute><ReviewSubmission /></ProtectedRoute>}
        />
        <Route
          path="/dashboard/claims/:id/submit"
          element={<ProtectedRoute><SubmitWork /></ProtectedRoute>}
        />
        <Route
          path="/dashboard/settings"
          element={<ProtectedRoute><Settings /></ProtectedRoute>}
        />
      </Routes>
    </Suspense>
  );
}

function AppRoutes() {
  const { marketplace_enabled } = useAppConfig();

  return (
    <Routes>
      <Route path="/landing" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />

      <Route element={<Layout />}>
        <Route path="/" element={<ProtectedRoute><Overview /></ProtectedRoute>} />
        <Route path="/dashboard" element={<Navigate to="/" replace />} />
        <Route path="/agents" element={<ProtectedRoute><AgentHealth /></ProtectedRoute>} />
        <Route path="/agents/:id" element={<ProtectedRoute><AgentDetail /></ProtectedRoute>} />
        <Route path="/trust" element={<ProtectedRoute><TrustScores /></ProtectedRoute>} />
        <Route path="/transactions" element={<ProtectedRoute><TransactionFlow /></ProtectedRoute>} />
        <Route path="/audit" element={<ProtectedRoute><AuditLog /></ProtectedRoute>} />
        <Route path="/policies" element={<ProtectedRoute><PolicyEditor /></ProtectedRoute>} />
        <Route path="/settlement" element={<ProtectedRoute><SettlementOverview /></ProtectedRoute>} />
        <Route path="/alerts" element={<ProtectedRoute><Alerts /></ProtectedRoute>} />
        <Route path="/assist" element={<ProtectedRoute><GatewayAssist /></ProtectedRoute>} />

        {marketplace_enabled && (
          <Route path="/marketplace/*" element={<MarketplaceRoutes />} />
        )}

        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  const { config, ready } = useAppConfigLoader();

  if (!ready) {
    return (
      <div className="flex items-center justify-center min-h-screen text-gray-400">
        Loading...
      </div>
    );
  }

  return (
    <AppConfigContext.Provider value={config}>
      <AppRoutes />
    </AppConfigContext.Provider>
  );
}
