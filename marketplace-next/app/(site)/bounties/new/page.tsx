import { RequireAuth } from "@/components/RequireAuth";
import PostBounty from "@/components/marketplace-pages/PostBounty";

export default function PostBountyPage() {
  return (
    <RequireAuth>
      <PostBounty />
    </RequireAuth>
  );
}
