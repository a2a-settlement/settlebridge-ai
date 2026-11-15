import { createContext, useContext, useEffect, useState } from "react";
import api from "@/services/api";

export interface AppConfig {
  marketplace_enabled: boolean;
  gateway_enabled: boolean;
  agent_management_v2: boolean;
}

const defaults: AppConfig = {
  marketplace_enabled: false,
  gateway_enabled: true,
  agent_management_v2: false,
};

const AppConfigContext = createContext<AppConfig>(defaults);

export function useAppConfig() {
  return useContext(AppConfigContext);
}

export { AppConfigContext };

export function useAppConfigLoader() {
  const [config, setConfig] = useState<AppConfig>(defaults);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    api
      .get<AppConfig>("/config")
      .then(({ data }) => setConfig(data))
      .catch(() => {})
      .finally(() => setReady(true));
  }, []);

  return { config, ready };
}
