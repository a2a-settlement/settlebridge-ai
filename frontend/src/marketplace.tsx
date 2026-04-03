import React, { lazy, Suspense } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AuthProvider } from "./hooks/useAuth";
import "./app.css";

import SharedSubmission from "./pages/SharedSubmission";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Register from "./pages/Register";

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

import { useAuth } from "./hooks/useAuth";
import { Navigate } from "react-router-dom";

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

function MarketplaceApp() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center py-20 text-gray-400">Loading...</div>}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/shared/:token" element={<SharedSubmission />} />

        <Route element={<Layout />}>
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
        </Route>
      </Routes>
    </Suspense>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter basename="/">
      <AuthProvider>
        <MarketplaceApp />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>
);
