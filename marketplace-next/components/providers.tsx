"use client";

import { type ReactNode, useMemo } from "react";
import { AuthProvider } from "@/hooks/useAuth";
import {
  AppConfigContext,
  type AppConfig,
  useAppConfigLoader,
} from "@/hooks/useAppConfig";

function AppConfigBridge({ children }: { children: ReactNode }) {
  const { config, ready } = useAppConfigLoader();
  const value = useMemo<AppConfig>(
    () => (ready ? config : { marketplace_enabled: true, gateway_enabled: true }),
    [config, ready]
  );
  return (
    <AppConfigContext.Provider value={value}>{children}</AppConfigContext.Provider>
  );
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <AppConfigBridge>{children}</AppConfigBridge>
    </AuthProvider>
  );
}
