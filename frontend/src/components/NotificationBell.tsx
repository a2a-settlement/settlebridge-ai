import { Bell } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "../services/api";
import type { NotificationListResponse, Notification } from "../types";

export default function NotificationBell() {
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState<Notification[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const fetch = () => {
      api
        .get<NotificationListResponse>("/notifications?limit=5")
        .then(({ data }) => {
          setUnread(data.unread_count);
          setItems(data.notifications);
        })
        .catch(() => {});
    };
    fetch();
    const interval = setInterval(fetch, 15000);
    return () => clearInterval(interval);
  }, []);

  const markAllRead = async () => {
    await api.post("/notifications/read-all");
    setUnread(0);
    setItems((prev) => prev.map((n) => ({ ...n, read: true })));
  };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="relative text-gray-400 hover:text-white transition"
      >
        <Bell className="w-5 h-5" />
        {unread > 0 && (
          <span className="absolute -top-1 -right-1 w-4 h-4 bg-danger rounded-full text-[10px] font-bold flex items-center justify-center text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setOpen(false)}
          />
          <div className="absolute right-0 mt-2 w-80 bg-white rounded-xl shadow-xl border border-gray-200 z-50 overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <span className="font-semibold text-navy-900 text-sm">
                Notifications
              </span>
              {unread > 0 && (
                <button
                  onClick={markAllRead}
                  className="text-xs text-navy-600 hover:underline"
                >
                  Mark all read
                </button>
              )}
            </div>
            <div className="max-h-72 overflow-y-auto">
              {items.length === 0 ? (
                <p className="p-4 text-sm text-gray-400 text-center">
                  No notifications
                </p>
              ) : (
                items.map((n) => (
                  <div
                    key={n.id}
                    className={`px-4 py-3 border-b last:border-0 text-sm ${
                      n.read ? "bg-white" : "bg-blue-50"
                    }`}
                  >
                    <p className="font-medium text-navy-900">{n.title}</p>
                    <p className="text-gray-500 text-xs mt-0.5">{n.message}</p>
                  </div>
                ))
              )}
            </div>
            <Link
              to="/dashboard/notifications"
              className="block text-center text-sm text-navy-600 hover:bg-gray-50 py-3 border-t"
              onClick={() => setOpen(false)}
            >
              View all
            </Link>
          </div>
        </>
      )}
    </div>
  );
}
