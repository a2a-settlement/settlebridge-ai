import Link from "next/link";

export const metadata = { title: "Notifications" };

/** Placeholder: full notifications UI can be added later. */
export default function NotificationsPage() {
  return (
    <div className="max-w-2xl mx-auto px-4 py-12 text-center text-gray-500">
      <p className="font-medium text-navy-900 mb-2">Notifications</p>
      <p className="text-sm mb-6">
        Use the bell icon in the header for recent activity, or check your email
        for updates.
      </p>
      <Link href="/dashboard" className="text-navy-600 hover:underline text-sm">
        Back to dashboard
      </Link>
    </div>
  );
}
