import { RequireAuth } from "@/components/RequireAuth";
import BountyAssist from "@/components/marketplace-pages/BountyAssist";

export default function BountyAssistPage() {
  return (
    <RequireAuth>
      <BountyAssist />
    </RequireAuth>
  );
}
