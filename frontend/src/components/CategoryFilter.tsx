import { useEffect, useState } from "react";
import api from "../services/api";
import type { Category } from "../types";

interface Props {
  selected: string | null;
  onChange: (id: string | null) => void;
}

export default function CategoryFilter({ selected, onChange }: Props) {
  const [categories, setCategories] = useState<Category[]>([]);

  useEffect(() => {
    api.get<Category[]>("/categories").then(({ data }) => setCategories(data));
  }, []);

  return (
    <div className="space-y-1">
      <button
        onClick={() => onChange(null)}
        className={`w-full text-left px-3 py-2 rounded-lg text-sm transition ${
          selected === null
            ? "bg-navy-900 text-white font-medium"
            : "text-gray-600 hover:bg-gray-100"
        }`}
      >
        All Categories
      </button>
      {categories.map((cat) => (
        <button
          key={cat.id}
          onClick={() => onChange(cat.id)}
          className={`w-full text-left px-3 py-2 rounded-lg text-sm transition ${
            selected === cat.id
              ? "bg-navy-900 text-white font-medium"
              : "text-gray-600 hover:bg-gray-100"
          }`}
        >
          {cat.name}
        </button>
      ))}
    </div>
  );
}
