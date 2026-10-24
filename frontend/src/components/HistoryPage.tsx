import { useEffect, useState } from "react";
import { fetchRuns, type Run } from "../api";
import RunView from "./RunView";

const STATUS: Record<string, string> = { queued: "в очереди", running: "идёт", done: "завершён", failed: "не удался" };
const STATUS_STYLE: Record<string, string> = {
  queued: "bg-slate-100 text-slate-600",
  running: "bg-blue-100 text-blue-800",
  done: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800",
};

/** Вкладка «История поисков»: все запуски; открываются без нового обращения к модели (всё хранится в базе). */
export default function HistoryPage({ runId, onOpen, onBack }: { runId: number | null; onOpen: (id: number) => void; onBack: () => void }) {
  const [runs, setRuns] = useState<Run[] | null>(null);

  useEffect(() => {
    if (runId === null) fetchRuns().then(setRuns).catch(() => setRuns([]));
  }, [runId]);

  if (runId !== null) {
    return (
      <div className="space-y-4">
        <button onClick={onBack} className="text-sm text-cyan-700 hover:underline">
          ← К истории поисков
        </button>
        <RunView key={runId} runId={runId} onFinished={() => undefined} />
      </div>
    );
  }
  if (runs === null) return <p className="text-sm text-slate-400">Загружаем историю…</p>;
  if (runs.length === 0) {
    return <p className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-500 shadow-sm">Поисков пока не было. Запустите первый на вкладке «Найти поставщика».</p>;
  }
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="mb-1 text-lg font-semibold">История поисков ({runs.length})</h2>
      <p className="mb-3 text-sm text-slate-500">Нажмите на поиск, чтобы открыть его результаты. Повторного платного запуска при этом не будет.</p>
      <ul className="divide-y divide-slate-100">
        {runs.map((r) => (
          <li key={r.id}>
            <button onClick={() => onOpen(r.id)} className="flex w-full flex-wrap items-baseline justify-between gap-x-3 gap-y-1 py-2.5 text-left text-sm hover:bg-slate-50">
              <span>
                <span className="font-medium">{r.category}</span>
                <span className="ml-2 text-xs text-slate-400">
                  {r.location ?? "Вся Россия"}
                  {r.include_neighbors ? " + соседние" : ""}
                  {r.supplier_type ? ` · ${r.supplier_type}` : ""}
                </span>
              </span>
              <span className="flex items-center gap-2 text-xs text-slate-400">
                {r.stats?.suppliers_saved ?? 0} пост. · {new Date(r.created_at).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" })}
                <span className={`rounded-full px-2 py-0.5 ${STATUS_STYLE[r.status] ?? "bg-slate-100 text-slate-600"}`}>{STATUS[r.status] ?? r.status}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
