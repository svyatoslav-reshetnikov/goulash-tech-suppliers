import { useEffect, useState } from "react";
import { fetchRuns, type Run } from "../api";
import RunView from "./RunView";

const STATUS: Record<string, string> = { queued: "в очереди", running: "идёт", done: "завершён", failed: "не удался" };
const STATUS_STYLE: Record<string, string> = {
  queued: "bg-slate-100 text-slate-700",
  running: "bg-blue-100 text-blue-800",
  done: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800",
};

function RunRow({ run, onOpen }: { run: Run; onOpen: (id: number) => void }) {
  return (
    <button onClick={() => onOpen(run.id)} className="flex w-full flex-wrap items-baseline justify-between gap-x-3 gap-y-1 py-2.5 text-left text-sm hover:bg-slate-50">
      <span>
        <span className="font-medium">{run.category}</span>
        <span className="ml-2 text-xs text-slate-500">
          {run.location ?? "Вся Россия"}
          {run.include_neighbors ? " + соседние" : ""}
        </span>
      </span>
      <span className="flex items-center gap-2 text-xs text-slate-500">
        {run.stats?.suppliers_saved ?? 0} пост. · {new Date(run.created_at).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" })}
        <span className={`rounded-full px-2 py-0.5 ${STATUS_STYLE[run.status] ?? "bg-slate-100 text-slate-700"}`}>{STATUS[run.status] ?? run.status}</span>
      </span>
    </button>
  );
}

/** Вкладка «История поисков»: все запуски. Результаты открываются без повторного запуска. */
export default function HistoryPage({ runId, onOpen, onBack }: { runId: number | null; onOpen: (id: number) => void; onBack: () => void }) {
  const [runs, setRuns] = useState<Run[] | null>(null);

  useEffect(() => {
    if (runId === null) fetchRuns().then(setRuns).catch(() => setRuns([]));
  }, [runId]);

  if (runId !== null) {
    return (
      <div className="space-y-4">
        <button onClick={onBack} className="text-sm text-cyan-800 hover:underline">
          ← К истории поисков
        </button>
        <RunView key={runId} runId={runId} onFinished={() => undefined} />
      </div>
    );
  }
  if (runs === null) return <p className="text-sm text-slate-500">Загружаем историю…</p>;
  if (runs.length === 0) {
    return <p className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-600 shadow-sm">Поисков пока не было. Запустите первый на вкладке «Найти поставщика».</p>;
  }
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="mb-1 text-lg font-semibold">История поисков ({runs.length})</h2>
      <p className="mb-3 text-sm text-slate-600">Нажмите на поиск, чтобы открыть его результаты. Повторного платного запуска при этом не будет.</p>
      <ul className="divide-y divide-slate-100">
        {runs.map((run) => (
          <li key={run.id}>
            <RunRow run={run} onOpen={onOpen} />
          </li>
        ))}
      </ul>
    </section>
  );
}
