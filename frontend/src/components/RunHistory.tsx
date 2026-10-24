import { useEffect, useState } from "react";
import { fetchRuns, type Run } from "../api";

const STATUS: Record<string, string> = { queued: "в очереди", running: "идёт", done: "завершён", failed: "не удался" };

/** Прошлые запуски: открываются без нового обращения к модели (всё хранится в базе). */
export default function RunHistory({ current, reloadKey, onOpen }: { current: number | null; reloadKey: number; onOpen: (id: number) => void }) {
  const [runs, setRuns] = useState<Run[]>([]);

  useEffect(() => {
    fetchRuns().then(setRuns).catch(() => setRuns([]));
  }, [reloadKey]);

  if (runs.length === 0) return null;
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="mb-3 text-sm font-medium text-slate-500">Прошлые запуски</h2>
      <ul className="divide-y divide-slate-100">
        {runs.map((r) => (
          <li key={r.id}>
            <button
              onClick={() => onOpen(r.id)}
              className={`flex w-full flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-2 text-left text-sm hover:bg-slate-50 ${current === r.id ? "font-medium" : ""}`}
            >
              <span>
                №{r.id} {r.category}
                <span className="ml-2 text-xs text-slate-400">{r.location ?? "Вся Россия"}</span>
              </span>
              <span className="text-xs text-slate-400">
                {r.stats?.suppliers_saved ?? 0} пост. · {STATUS[r.status] ?? r.status} · {new Date(r.created_at).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" })}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
