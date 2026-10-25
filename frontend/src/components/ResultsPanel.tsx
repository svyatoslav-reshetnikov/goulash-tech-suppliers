import { useEffect, useRef, useState } from "react";
import { exportUrl, setSupplierStatus, type SupplierData, type SupplierStatus } from "../api";
import { usePersistedState } from "../lib/persist";
import { sortSuppliers, type SortState } from "../lib/results";
import { STATUS_LABELS, STATUS_ORDER, statusOf } from "../lib/status";
import ComparisonTable from "./ComparisonTable";
import SupplierCard from "./SupplierCard";
import SupplierTable from "./SupplierTable";

export const MAX_COMPARE = 4;

type UiState = { view: "cards" | "table"; expanded: number[]; sources: boolean; sort: SortState; statusFilter: "all" | SupplierStatus };
const DEFAULT_UI: UiState = { view: "cards", expanded: [], sources: false, sort: { key: "rank", dir: "asc" }, statusFilter: "all" };

type Props = {
  runId: number;
  suppliers: SupplierData[];
  live: boolean;
  onNote: (supplierId: number, note: string | null) => void;
  /** Called with the new status right away (optimistic) and with the previous one if saving fails. */
  onStatus: (supplierId: number, status: SupplierStatus, updatedAt: string | null) => void;
};

/** Result list of one search: cards or table, expand/collapse, source toggle, status filter, export and comparison. UI state is remembered per search. */
export default function ResultsPanel({ runId, suppliers, live, onNote, onStatus }: Props) {
  const [ui, setUi] = usePersistedState<UiState>(`goulash:results:${runId}`, DEFAULT_UI);
  const [selected, setSelected] = useState<number[]>([]);
  const [comparing, setComparing] = useState(false);
  const [statusError, setStatusError] = useState(false);
  const comparison = useRef<HTMLDivElement>(null);
  const state = { ...DEFAULT_UI, ...ui }; // state saved by an older version of the page may lack newer keys

  useEffect(() => {
    setSelected([]);
    setComparing(false);
  }, [runId]);

  const shown = state.statusFilter === "all" ? suppliers : suppliers.filter((s) => statusOf(s) === state.statusFilter);
  const ordered = state.view === "table" ? sortSuppliers(shown, state.sort) : shown;
  const toggleSelect = (id: number) => setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : prev.length < MAX_COMPARE ? [...prev, id] : prev));
  const toggleExpanded = (id: number) =>
    setUi((prev) => ({ ...prev, expanded: prev.expanded.includes(id) ? prev.expanded.filter((x) => x !== id) : [...prev.expanded, id] }));
  const allExpanded = shown.length > 0 && shown.every((s) => state.expanded.includes(s.id));
  const chosen = suppliers.filter((s) => selected.includes(s.id));

  const openComparison = () => {
    setComparing(true);
    setTimeout(() => comparison.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };

  const changeStatus = async (supplierId: number, status: SupplierStatus) => {
    const previous = suppliers.find((s) => s.id === supplierId)?.status;
    setStatusError(false);
    onStatus(supplierId, status, null);
    try {
      const saved = await setSupplierStatus(runId, supplierId, status);
      onStatus(supplierId, saved.value, saved.updated_at);
    } catch {
      onStatus(supplierId, previous?.value ?? "new", previous?.updated_at ?? null);
      setStatusError(true);
    }
  };

  // the file follows what is on the screen: chosen suppliers, otherwise the shown list, in the order of the table
  const exportIds = (selected.length > 0 ? ordered.filter((s) => selected.includes(s.id)) : ordered).map((s) => s.id);
  const exportLabel = selected.length > 0 ? `выбранные: ${selected.length}` : state.statusFilter !== "all" ? `показанные: ${shown.length}` : `все: ${suppliers.length}`;

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
              aria-pressed={state.view === v}
              className={`px-3 py-1 ${state.view === v ? "bg-slate-900 text-white" : "bg-white text-slate-700 hover:bg-slate-50"}`}
            >
              {v === "cards" ? "Карточки" : "Таблица"}
            </button>
          ))}
        </div>
        {state.view === "cards" && (
          <button
            onClick={() => setUi((prev) => ({ ...prev, expanded: allExpanded ? [] : shown.map((s) => s.id) }))}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1 text-slate-700 hover:bg-slate-50"
          >
            {allExpanded ? "Свернуть все" : "Развернуть все"}
          </button>
        )}
        <label className="flex items-center gap-1.5 text-slate-700">
          <input type="checkbox" checked={state.sources} onChange={(e) => setUi((prev) => ({ ...prev, sources: e.target.checked }))} />
          Показывать источники
        </label>
        <label className="flex items-center gap-1.5 text-slate-700">
          Статус:
          <select
            value={state.statusFilter}
            onChange={(e) => setUi((prev) => ({ ...prev, statusFilter: e.target.value as UiState["statusFilter"] }))}
            aria-label="Фильтр по статусу"
            className="rounded-lg border border-slate-300 bg-white px-2 py-1"
          >
            <option value="all">все</option>
            {STATUS_ORDER.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABELS[s]} ({suppliers.filter((x) => statusOf(x) === s).length})
              </option>
            ))}
          </select>
        </label>
        <span className="ml-auto inline-flex overflow-hidden rounded-lg border border-slate-300">
          <a
            href={exportUrl(runId, "xlsx", exportIds)}
            download
            className="bg-white px-3 py-1 text-slate-800 hover:bg-slate-50"
            aria-label={`Скачать в Excel (${exportLabel})`}
          >
            Скачать в Excel <span className="text-slate-500">({exportLabel})</span>
          </a>
          <a href={exportUrl(runId, "csv", exportIds)} download className="border-l border-slate-300 bg-white px-2 py-1 text-slate-600 hover:bg-slate-50" title="Файл CSV: запасной вариант">
            CSV
          </a>
        </span>
      </div>
      {statusError && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">Не удалось сохранить статус. Попробуйте ещё раз.</p>}

      {shown.length === 0 ? (
        <p className="rounded-lg bg-slate-50 px-3 py-4 text-sm text-slate-600">Нет поставщиков со статусом «{state.statusFilter === "all" ? "" : STATUS_LABELS[state.statusFilter]}».</p>
      ) : state.view === "cards" ? (
        <div className="space-y-3">
          {shown.map((s) => (
            <SupplierCard
              key={s.id}
              supplier={s}
              expanded={state.expanded.includes(s.id)}
              onToggleExpanded={() => toggleExpanded(s.id)}
              showSources={state.sources}
              selected={selected.includes(s.id)}
              canSelect={selected.length < MAX_COMPARE}
              onToggleSelect={() => toggleSelect(s.id)}
              onStatus={changeStatus}
              onNote={onNote}
            />
          ))}
        </div>
      ) : (
        <SupplierTable
          suppliers={shown}
          selected={selected}
          onToggleSelect={toggleSelect}
          canSelectMore={selected.length < MAX_COMPARE}
          sort={state.sort}
          onSort={(sort) => setUi((prev) => ({ ...prev, sort }))}
          onStatus={changeStatus}
        />
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
