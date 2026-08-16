import { RequireAuth } from "@/components/RequireAuth";
import CreateContract from "@/components/marketplace-pages/CreateContract";

export default function NewContractPage() {
  return (
    <RequireAuth>
      <CreateContract />
    </RequireAuth>
  );
}
