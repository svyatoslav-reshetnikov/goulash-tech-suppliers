import { useEffect, useRef, useState } from "react";
import { fetchRun, fetchRunSuppliers, subscribeRun, type Run, type RunEvent, type SupplierData } from "../api";
import ComparisonTable from "./ComparisonTable";
import SupplierCard from "./SupplierCard";

const KINDS: Record<string, { label: string; style: string }> = {
  run_started: { label: "Старт", style: "bg-slate-100 text-slate-700" },
  search: { label: "Поиск", style: "bg-blue-100 text-blue-800" },
  search_result: { label: "Выдача", style: "bg-blue-50 text-blue-700" },
  fetch: { label: "Страница", style: "bg-violet-100 text-violet-800" },
  research: { label: "Изучение", style: "bg-indigo-100 text-indigo-800" },
  research_extract: { label: "Анализ", style: "bg-indigo-50 text-indigo-700" },
  research_more: { label: "Доп. поиск", style: "bg-amber-50 text-amber-700" },
  research_failed: { label: "Не открылся", style: "bg-red-50 text-red-700" },
  research_done: { label: "Сайт готов", style: "bg-emerald-50 text-emerald-700" },
  supplier_saved: { label: "Сохранён", style: "bg-emerald-100 text-emerald-800" },
  supplier_rejected: { label: "Отклонено", style: "bg-amber-100 text-amber-800" },
  note: { label: "Заметка", style: "bg-slate-100 text-slate-600" },
  run_finished: { label: "Итог", style: "bg-cyan-100 text-cyan-800" },
  run_failed: { label: "Ошибка", style: "bg-red-100 text-red-800" },
};
const STOP: Record<string, string> = {
  finished: "Агент завершил поиск",
  target_reached: "Найдено нужное число поставщиков",
  turn_limit: "Исчерпан лимит шагов",
  time_limit: "Исчерпан лимит времени",
  token_limit: "Исчерпан лимит токенов (бюджет запуска)",
  no_action: "Агент остановился без итогового действия",
  llm_error: "Ошибка модели",
};
const clock = (iso?: string) => (iso ? new Date(iso).toLocaleTimeString("ru-RU") : "");

/** Ход одного запуска: живая лента шагов агента и карточки найденных поставщиков, которые появляются по мере сохранения. */
export default function RunView({ runId, onFinished }: { runId: number; onFinished: () => void }) {
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [suppliers, setSuppliers] = useState<SupplierData[]>([]);
  const [live, setLive] = useState(true);
  const [userOpen, setUserOpen] = useState<boolean | null>(null); // null — решает ход поиска: пока идёт, блок раскрыт, после завершения свёрнут
  const [selected, setSelected] = useState<number[]>([]);
  const [comparing, setComparing] = useState(false);
  const feed = useRef<HTMLUListElement>(null);
  const comparison = useRef<HTMLDivElement>(null);
  const shown = events.filter((e) => e.type !== "usage");
  const inProgress = run === null ? false : run.status === "queued" || run.status === "running";
  const expanded = userOpen ?? inProgress;

  useEffect(() => {
    setRun(null);
    setEvents([]);
    setSuppliers([]);
    setSelected([]);
    setComparing(false);
    setLive(true);
    setUserOpen(null);
    const refresh = () => {
      void fetchRun(runId).then(setRun);
      void fetchRunSuppliers(runId).then(setSuppliers);
    };
    refresh();
    const stop = subscribeRun(
      runId,
      (event) => {
        setEvents((prev) => [...prev, event]);
        if (event.type === "supplier_saved") void fetchRunSuppliers(runId).then(setSuppliers);
      },
      () => {
        setLive(false);
        refresh();
        onFinished();
      },
    );
    return stop;
  }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    feed.current?.scrollTo({ top: feed.current.scrollHeight });
  }, [shown.length, expanded]);

  const searches = events.filter((e) => e.type === "search").length;
  const pages = events.filter((e) => e.type === "fetch").length;
  const tokens = (run?.stats?.prompt_tokens ?? 0) + (run?.stats?.completion_tokens ?? 0);
  const usage = [...events].reverse().find((e) => e.type === "usage");
  const liveCost = usage ? Number(usage.cost_rub) : (run?.stats?.cost_rub ?? null);
  const liveTokens = usage ? Number(usage.prompt_tokens) + Number(usage.completion_tokens) : tokens;

  return (
    <section className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
        <button
          onClick={() => setUserOpen(!expanded)}
          aria-expanded={expanded}
          className="flex w-full flex-wrap items-center justify-between gap-2 p-5 text-left"
        >
          <span className="flex items-baseline gap-2">
            <svg viewBox="0 0 20 20" className={`h-4 w-4 shrink-0 translate-y-0.5 fill-slate-400 transition-transform ${expanded ? "rotate-90" : ""}`} aria-hidden="true">
              <path d="M7 4l7 6-7 6z" />
            </svg>
            <span className="text-lg font-semibold">
              {run ? run.category : "Поиск"}
              <span className="font-normal text-slate-500">
                {" · "}
                {run ? (run.location ?? "Вся Россия") : "…"}
                {run?.include_neighbors && run.regions.length > 1 ? " и соседние регионы" : ""}
              </span>
            </span>
          </span>
          <span className="flex items-center gap-3">
            {!expanded && suppliers.length > 0 && (
              <span className="text-sm text-slate-500">
                поставщиков: {suppliers.length}
                {liveCost !== null ? ` · ${liveCost.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ₽` : ""}
              </span>
            )}
            <span
              className={`rounded-full px-3 py-1 text-xs font-medium ${
                live ? "bg-blue-100 text-blue-800" : run?.status === "failed" ? "bg-red-100 text-red-800" : "bg-emerald-100 text-emerald-800"
              }`}
            >
              {live ? "Идёт поиск…" : run?.status === "failed" ? "Не удалось" : "Завершён"}
            </span>
          </span>
        </button>
        {expanded && (
          <div className="border-t border-slate-100 px-5 pb-5 pt-3">
        {run?.supplier_type && <p className="text-sm text-slate-500">Тип поставщика: {run.supplier_type}</p>}
        <p className="mt-1 text-sm text-slate-600">
          Поисков: <b>{searches}</b> · открыто страниц: <b>{pages}</b> · найдено поставщиков: <b>{suppliers.length}</b>
        </p>

        {liveTokens > 0 && (
          <p className="mt-1 text-sm text-slate-600">
            Расход{live ? " сейчас" : ""}:{" "}
            {liveCost !== null && <b>{liveCost.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ₽ · </b>}
            <span className="text-slate-500">{liveTokens.toLocaleString("ru-RU")} токенов{liveCost !== null ? ", оценка по тарифу" : ""}</span>
          </p>
        )}

        <ul ref={feed} className="mt-3 max-h-72 space-y-1.5 overflow-y-auto overflow-x-hidden rounded-lg bg-slate-50 p-3">
          {shown.length === 0 && <li className="text-sm text-slate-400">Ждём первых шагов агента…</li>}
          {shown.map((e) => {
            const kind = KINDS[e.type] ?? { label: e.type, style: "bg-slate-100 text-slate-600" };
            return (
              <li key={e.seq ?? e.message} className="grid grid-cols-[3.6rem_5.2rem_minmax(0,1fr)] items-baseline gap-2 text-sm">
                <span className="text-xs text-slate-400">{clock(e.created_at)}</span>
                <span className={`rounded px-1.5 py-0.5 text-center text-xs ${kind.style}`}>{kind.label}</span>
                <span className="break-words text-slate-700">{e.message}</span>
              </li>
            );
          })}
        </ul>

        {!live && run && (
          <div className="mt-3 text-sm text-slate-600">
            <p>
              {run.error ? <span className="text-red-600">Ошибка: {run.error}. </span> : null}
              {run.stop_reason ? `${STOP[run.stop_reason] ?? run.stop_reason}. ` : ""}
              {tokens > 0 && `Использовано ${tokens.toLocaleString("ru-RU")} токенов за ${run.stats?.seconds ?? "?"} с${run.stats?.cost_rub != null ? `, примерно ${run.stats.cost_rub} ₽ (модель и поиск по тарифу)` : ""}.`}
            </p>
            {run.summary && !run.summary.startsWith("[TOOL_CALL") && !run.summary.startsWith("Сохранено поставщиков:") && (
              <p className="mt-1 text-slate-500">{run.summary}</p>
            )}
          </div>
        )}
          </div>
        )}
      </div>

      {comparing && selected.length >= 2 && (
        <div ref={comparison}>
          <ComparisonTable
            suppliers={suppliers.filter((s) => selected.includes(s.id))}
            onClose={() => setComparing(false)}
            onRemove={(id) => setSelected((prev) => prev.filter((x) => x !== id))}
          />
        </div>
      )}

      {suppliers.length > 0 && (
        <div className="space-y-3">
          <div>
            <h2 className="text-lg font-semibold">Найденные поставщики ({suppliers.length})</h2>
            <p className="text-sm text-slate-500">
              По рейтингу, лучшие отмечены «Рекомендуем связаться». Чтобы сравнить, отметьте галочкой «Сравнить» у 2–4 поставщиков: внизу появится кнопка.
            </p>
            {!live && !suppliers.some((s) => s.ranking.recommended) && (
              <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
                Рекомендовать пока некого: у найденных поставщиков нет телефона, не тот тип или слишком мало подтверждённых данных (оценка ниже 40 из 100).
              </p>
            )}
          </div>
          {suppliers.map((s) => (
            <SupplierCard
              key={s.id}
              supplier={s}
              selected={selected.includes(s.id)}
              canSelect={selected.length < 4}
              onToggle={() => setSelected((prev) => (prev.includes(s.id) ? prev.filter((x) => x !== s.id) : [...prev, s.id]))}
              onNote={(id, note) => setSuppliers((prev) => prev.map((x) => (x.id === id ? { ...x, note } : x)))}
            />
          ))}
        </div>
      )}
      {selected.length > 0 && (
        <div className="fixed inset-x-0 bottom-4 z-10 mx-auto flex w-fit max-w-[calc(100%-2rem)] flex-wrap items-center gap-3 rounded-xl bg-slate-900 px-4 py-2.5 text-sm text-white shadow-lg">
          <span>
            Для сравнения выбрано: {selected.length} из 4{selected.length < 2 ? ". Выберите ещё хотя бы одного" : ""}
          </span>
          <button
            onClick={() => {
              setComparing(true);
              setTimeout(() => comparison.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
            }}
            disabled={selected.length < 2}
            className="rounded-lg bg-white px-3 py-1 font-medium text-slate-900 hover:bg-slate-200 disabled:opacity-40"
          >
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
      {!live && suppliers.length === 0 && run?.status !== "failed" && (
        <p className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">Подходящих поставщиков не найдено. Попробуйте другую категорию или локацию.</p>
      )}
    </section>
  );
}
