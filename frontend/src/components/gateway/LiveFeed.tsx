import { useEffect, useRef } from "react";

interface LiveFeedProps<T> {
  items: T[];
  renderItem: (item: T, index: number) => React.ReactNode;
  maxItems?: number;
  emptyMessage?: string;
}

export default function LiveFeed<T>({
  items,
  renderItem,
  maxItems = 50,
  emptyMessage = "No activity yet",
}: LiveFeedProps<T>) {
  const containerRef = useRef<HTMLDivElement>(null);
  const autoScroll = useRef(true);

  useEffect(() => {
    const el = containerRef.current;
    if (el && autoScroll.current) {
      el.scrollTop = 0;
    }
  }, [items]);

  const handleScroll = () => {
    const el = containerRef.current;
    if (el) {
      autoScroll.current = el.scrollTop < 10;
    }
  };

  const visible = items.slice(0, maxItems);

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="overflow-y-auto max-h-96 divide-y divide-gray-100"
    >
      {visible.length === 0 ? (
        <p className="text-center text-gray-400 py-8 text-sm">{emptyMessage}</p>
      ) : (
        visible.map((item, i) => (
          <div key={i} className="py-2 px-1 transition-colors hover:bg-gray-50">
            {renderItem(item, i)}
          </div>
        ))
      )}
    </div>
  );
}
