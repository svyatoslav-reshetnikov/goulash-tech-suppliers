import type { Ranking } from "../api";

const TIER_STYLE: Record<Ranking["tier"], string> = {
  local: "bg-emerald-100 text-emerald-800",
  delivers: "bg-cyan-100 text-cyan-800",
  neighbor: "bg-blue-100 text-blue-800",
  other: "bg-amber-100 text-amber-800",
  unknown: "bg-slate-100 text-slate-600",
  any: "bg-slate-100 text-slate-600",
};

/** Место в рейтинге, оценка из 100 и метка локации поставщика относительно региона поиска. */
export default function RankBadge({ ranking }: { ranking: Ranking }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <span className="rounded bg-slate-900 px-2 py-0.5 text-xs font-semibold text-white">#{ranking.rank}</span>
      <span className="text-sm font-semibold">{ranking.score}</span>
      <span className="text-xs text-slate-400">из 100</span>
      {ranking.tier !== "any" && (
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${TIER_STYLE[ranking.tier]}`}>{ranking.tier_label}</span>
      )}
    </span>
  );
}
