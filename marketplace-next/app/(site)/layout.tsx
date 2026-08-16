import MarketplaceHeader from "@/components/MarketplaceHeader";

export default function SiteLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-gray-50">
      <MarketplaceHeader />
      <main>{children}</main>
    </div>
  );
}
