import GitHubIdentity from "@/components/marketplace-pages/GitHubIdentity";

export const metadata = { title: "Link GitHub" };

export default function GitHubSettingsPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <GitHubIdentity />
    </div>
  );
}
