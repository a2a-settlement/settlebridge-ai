import { Star } from "lucide-react";

interface Props {
  score: number | null;
  size?: "sm" | "md" | "lg";
}

export default function ReputationScore({ score, size = "md" }: Props) {
  if (score == null) {
    return (
      <span className="text-gray-400 text-sm italic">No reputation data</span>
    );
  }

  const pct = Math.round(score * 100);
  const color =
    pct >= 80
      ? "text-money-dark"
      : pct >= 60
        ? "text-yellow-600"
        : "text-red-500";

  const sizeClass =
    size === "lg"
      ? "text-2xl"
      : size === "md"
        ? "text-base"
        : "text-sm";

  return (
    <span className={`inline-flex items-center gap-1 font-semibold ${color} ${sizeClass}`}>
      <Star className={size === "lg" ? "w-6 h-6" : size === "md" ? "w-4 h-4" : "w-3.5 h-3.5"} />
      {pct}%
    </span>
  );
}
