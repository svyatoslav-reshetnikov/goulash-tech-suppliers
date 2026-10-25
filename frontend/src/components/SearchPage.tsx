import { useEffect, useRef, useState } from "react";
import { fetchGroup, fetchRun } from "../api";
import RunView from "./RunView";
import SearchForm from "./SearchForm";

/** Вкладка «Найти поставщика»: форма и ход запуска (для нескольких товаров: ход каждого поиска). Открытый запуск запоминается в адресе (#search/3). */
export default function SearchPage({ runId, initialProduct, onOpen }: { runId: number | null; initialProduct?: number | null; onOpen: (id: number) => void }) {
  const [running, setRunning] = useState(false);
  const [runIds, setRunIds] = useState<number[]>([]); // searches shown: one, or all of a multi-product group
  const [finished, setFinished] = useState<number[]>([]);
  const view = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setFinished([]);
    if (runId === null) {
      setRunIds([]);
      return;
    }
    setRunIds([runId]);
    // a run of a multi-product search is shown together with the other searches of its group
    void fetchRun(runId)
      .then((run) => (run.group_id ? fetchGroup(run.group_id) : null))
      .then((runs) => runs && setRunIds(runs.map((r) => r.id)))
      .catch(() => undefined);
    setTimeout(() => view.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
  }, [runId]);

  const markFinished = (id: number) => {
    setFinished((prev) => {
      const next = prev.includes(id) ? prev : [...prev, id];
      if (runIds.every((x) => next.includes(x))) setRunning(false);
      return next;
    });
  };

  return (
    <div className="space-y-5">
      <SearchForm busy={running} initialProduct={initialProduct} onOpenPrevious={onOpen} onStarted={(id) => { setRunning(true); onOpen(id); }} />
      {runIds.length > 0 && (
        <div ref={view} className="space-y-5">
          {runIds.length > 1 && <p className="text-sm text-slate-600">Поиск по нескольким товарам: {runIds.length} поисков идут по очереди.</p>}
          {runIds.map((id) => (
            <RunView key={id} runId={id} onFinished={() => markFinished(id)} />
          ))}
        </div>
      )}
    </div>
  );
}
