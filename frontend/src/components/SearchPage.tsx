import { useState } from "react";
import RunHistory from "./RunHistory";
import RunView from "./RunView";
import SearchForm from "./SearchForm";

/** Вкладка «Поиск»: форма, ход текущего запуска и история. Открытый запуск запоминается в адресе (#search/3). */
export default function SearchPage({ runId, onOpen }: { runId: number | null; onOpen: (id: number) => void }) {
  const [reload, setReload] = useState(0);
  const [running, setRunning] = useState(false);

  return (
    <div className="space-y-5">
      <SearchForm
        busy={running}
        onStarted={(id) => {
          setRunning(true);
          onOpen(id);
          setReload((k) => k + 1);
        }}
      />
      {runId !== null && (
        <RunView
          key={runId}
          runId={runId}
          onFinished={() => {
            setRunning(false);
            setReload((k) => k + 1);
          }}
        />
      )}
      <RunHistory current={runId} reloadKey={reload} onOpen={onOpen} />
    </div>
  );
}
