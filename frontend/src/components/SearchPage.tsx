import { useEffect, useRef, useState } from "react";
import RunView from "./RunView";
import SearchForm from "./SearchForm";

/** Вкладка «Найти поставщика»: форма и ход запуска. Открытый запуск запоминается в адресе (#search/3). */
export default function SearchPage({ runId, onOpen }: { runId: number | null; onOpen: (id: number) => void }) {
  const [running, setRunning] = useState(false);
  const view = useRef<HTMLDivElement>(null);

  // открыли запуск (новый или прошлый): показываем его, а не оставляем пользователя наверху формы
  useEffect(() => {
    if (runId !== null) setTimeout(() => view.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
  }, [runId]);

  return (
    <div className="space-y-5">
      <SearchForm
        busy={running}
        onOpenPrevious={onOpen}
        onStarted={(id) => {
          setRunning(true);
          onOpen(id);
        }}
      />
      {runId !== null && (
        <div ref={view}>
          <RunView key={runId} runId={runId} onFinished={() => setRunning(false)} />
        </div>
      )}
    </div>
  );
}
