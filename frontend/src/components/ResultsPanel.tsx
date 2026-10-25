import { useEffect, useRef, useState } from "react";
import type { SupplierData } from "../api";
import { usePersistedState } from "../lib/persist";
import ComparisonTable from "./ComparisonTable";
import SupplierCard from "./SupplierCard";
import SupplierTable from "./SupplierTable";

export const MAX_COMPARE = 4;

type UiState = { view: "cards" | "table"; expanded: number[]; sources: boolean };
const DEFAULT_UI: UiState = { view: "cards", expanded: [], sources: false };

type Props = {
  runId: number;
  suppliers: SupplierData[];
  live: boolean;
  onNote: (supplierId: number, note: string | null) => void;
};

/** Result list of one search: cards or table, expand/collapse, source toggle and comparison. UI state is remembered per search. */
export default function ResultsPanel({ runId, suppliers, live, onNote }: Props) {
  const [ui, setUi] = usePersistedState<UiState>(`goulash:results:${runId}`, DEFAULT_UI);
  const [selected, setSelected] = useState<number[]>([]);
  const [comparing, setComparing] = useState(false);
  const comparison = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setSelected([]);
    setComparing(false);
  }, [runId]);

  const toggleSelect = (id: number) => setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : prev.length < MAX_COMPARE ? [...prev, id] : prev));
  const toggleExpanded = (id: number) =>
    setUi((prev) => ({ ...prev, expanded: prev.expanded.includes(id) ? prev.expanded.filter((x) => x !== id) : [...prev.expanded, id] }));
  const allExpanded = suppliers.length > 0 && suppliers.every((s) => ui.expanded.includes(s.id));
  const chosen = suppliers.filter((s) => selected.includes(s.id));

  const openComparison = () => {
    setComparing(true);
    setTimeout(() => comparison.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };

  return (
    <div className="space-y-3">
      {comparing && chosen.length >= 2 && (
        <div ref={comparison}>
          <ComparisonTable suppliers={chosen} onClose={() => setComparing(false)} onRemove={(id) => setSelected((prev) => prev.filter((x) => x !== id))} />
        </div>
      )}

      <div>
        <h2 className="text-lg font-semibold">Найденные поставщики ({suppliers.length})</h2>
        <p className="text-sm text-slate-600">
          По рейтингу, лучшие отмечены «Рекомендуем связаться». Чтобы сравнить, отметьте галочкой «Сравнить» у 2–4 поставщиков: внизу появится кнопка.
        </p>
        {!live && !suppliers.some((s) => s.ranking.recommended) && (
          <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
            Рекомендовать пока некого: ни у одного поставщика нет подтверждённого товара, телефона и известного региона одновременно.
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <div role="group" aria-label="Вид списка" className="inline-flex overflow-hidden rounded-lg border border-slate-300">
          {(["cards", "table"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setUi((prev) => ({ ...prev, view: v }))}
              aria-pressed={ui.view === v}
              className={`px-3 py-1 ${ui.view === v ? "bg-slate-900 text-white" : "bg-white text-slate-700 hover:bg-slate-50"}`}
            >
              {v === "cards" ? "Карточки" : "Таблица"}
            </button>
          ))}
        </div>
        {ui.view === "cards" && (
          <button
            onClick={() => setUi((prev) => ({ ...prev, expanded: allExpanded ? [] : suppliers.map((s) => s.id) }))}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1 text-slate-700 hover:bg-slate-50"
          >
            {allExpanded ? "Свернуть все" : "Развернуть все"}
          </button>
        )}
        <label className="flex items-center gap-1.5 text-slate-700">
          <input type="checkbox" checked={ui.sources} onChange={(e) => setUi((prev) => ({ ...prev, sources: e.target.checked }))} />
          Показывать источники
        </label>
      </div>

      {ui.view === "cards" ? (
        <div className="space-y-3">
          {suppliers.map((s) => (
            <SupplierCard
              key={s.id}
              supplier={s}
              expanded={ui.expanded.includes(s.id)}
              onToggleExpanded={() => toggleExpanded(s.id)}
              showSources={ui.sources}
              selected={selected.includes(s.id)}
              canSelect={selected.length < MAX_COMPARE}
              onToggleSelect={() => toggleSelect(s.id)}
              onNote={onNote}
            />
          ))}
        </div>
      ) : (
        <SupplierTable suppliers={suppliers} selected={selected} onToggleSelect={toggleSelect} canSelectMore={selected.length < MAX_COMPARE} />
      )}

      {selected.length > 0 && (
        <div
          role="region"
          aria-label="Сравнение"
          className="fixed inset-x-0 bottom-4 z-10 mx-auto flex w-fit max-w-[calc(100%-2rem)] flex-wrap items-center gap-3 rounded-xl bg-slate-900 px-4 py-2.5 text-sm text-white shadow-lg"
        >
          <span>
            Для сравнения выбрано: {selected.length} из {MAX_COMPARE}
            {selected.length < 2 ? ". Выберите ещё хотя бы одного" : ""}
          </span>
          <button onClick={openComparison} disabled={selected.length < 2} className="rounded-lg bg-white px-3 py-1 font-medium text-slate-900 hover:bg-slate-200 disabled:opacity-40">
            Сравнить
          </button>
          <button
            onClick={() => {
              setSelected([]);
              setComparing(false);
            }}
            className="text-slate-300 hover:text-white"
          >
            Сбросить
          </button>
        </div>
      )}
    </div>
  );
}
