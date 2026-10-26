import { useEffect, useRef, useState } from "react";
import { fetchRun, fetchRunSuppliers, subscribeRun, type Run, type RunEvent, type SupplierData } from "../api";
import ResultsPanel from "./ResultsPanel";

const KINDS: Record<string, { label: string; style: string }> = {
  run_started: { label: "Старт", style: "bg-slate-100 text-slate-700" },
  search: { label: "Поиск", style: "bg-blue-100 text-blue-800" },
  search_result: { label: "Выдача", style: "bg-blue-50 text-blue-700" },
  search_error: { label: "Сбой поиска", style: "bg-red-50 text-red-700" },
  fetch: { label: "Страница", style: "bg-violet-100 text-violet-800" },
  research: { label: "Изучение", style: "bg-indigo-100 text-indigo-800" },
  research_extract: { label: "Анализ", style: "bg-indigo-50 text-indigo-700" },
  research_more: { label: "Доп. поиск", style: "bg-amber-50 text-amber-700" },
  research_failed: { label: "Не открылся", style: "bg-red-50 text-red-700" },
  research_skipped: { label: "Не подходит", style: "bg-slate-100 text-slate-600" },
  research_done: { label: "Сайт готов", style: "bg-emerald-50 text-emerald-700" },
  supplier_saved: { label: "Сохранён", style: "bg-emerald-100 text-emerald-800" },
  supplier_rejected: { label: "Отклонено", style: "bg-amber-100 text-amber-800" },
  note: { label: "Заметка", style: "bg-slate-100 text-slate-600" },
  run_finished: { label: "Итог", style: "bg-cyan-100 text-cyan-800" },
  run_failed: { label: "Ошибка", style: "bg-red-100 text-red-800" },
};
const CANDIDATE: Record<string, string> = {
  saved: "поставщик найден",
  unconfirmed: "товара нет на сайте",
  rejected: "не подходит",
  failed: "сайт не открылся",
  no_contacts: "нет контактов",
  studying: "изучается",
  pending: "не дошли",
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
  const feed = useRef<HTMLUListElement>(null);
  const shown = events.filter((e) => e.type !== "usage");
  const inProgress = run === null ? false : run.status === "queued" || run.status === "running";
  const expanded = userOpen ?? inProgress;

  useEffect(() => {
    setRun(null);
    setEvents([]);
    setSuppliers([]);
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
              </span>
            )}
            <span
              className={`rounded-full px-3 py-1 text-xs font-medium ${
                live ? "bg-blue-100 text-blue-800" : run?.status === "failed" ? "bg-red-100 text-red-800" : "bg-emerald-100 text-emerald-800"
              }`}
            >
              {live ? (run?.status === "queued" ? "В очереди" : "Идёт поиск…") : run?.status === "failed" ? "Не удалось" : "Завершён"}
            </span>
          </span>
        </button>
        {expanded && (
          <div className="border-t border-slate-100 px-5 pb-5 pt-3">
        <p className="mt-1 text-sm text-slate-600">
          Поисков: <b>{searches}</b> · открыто страниц: <b>{pages}</b> · найдено поставщиков: <b>{suppliers.length}</b>
        </p>

        {liveTokens > 0 && (
          <p className="mt-1 text-sm text-slate-600">
            Расход{live ? " сейчас" : ""}: <span className="text-slate-500">{liveTokens.toLocaleString("ru-RU")} токенов</span>
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

        {!live && run && (run.stats?.candidates?.length ?? 0) > 0 && (
          <details className="mt-3 text-sm text-slate-600">
            <summary className="cursor-pointer text-slate-700">Очередь кандидатов: {run.stats?.candidates?.length}</summary>
            <ul className="mt-2 max-h-72 space-y-1 overflow-y-auto rounded-lg bg-slate-50 p-3">
              {run.stats?.candidates?.map((c) => (
                <li key={c.domain} className="grid grid-cols-[minmax(0,12rem)_8.5rem_minmax(0,1fr)] items-baseline gap-2">
                  <span className="break-all text-slate-800">{c.domain}</span>
                  <span className="text-xs text-slate-500">{CANDIDATE[c.status] ?? c.status}</span>
                  <span className="break-words text-xs text-slate-500">{c.reason}</span>
                </li>
              ))}
            </ul>
          </details>
        )}

        {!live && run && (
          <div className="mt-3 text-sm text-slate-600">
            <p>
              {run.error ? <span className="text-red-600">Ошибка: {run.error}. </span> : null}
              {run.stop_reason ? `${STOP[run.stop_reason] ?? run.stop_reason}. ` : ""}
              {tokens > 0 && `Использовано ${tokens.toLocaleString("ru-RU")} токенов за ${run.stats?.seconds ?? "?"} с.`}
            </p>
            {run.summary && !run.summary.startsWith("[TOOL_CALL") && !run.summary.startsWith("Сохранено поставщиков:") && (
              <p className="mt-1 text-slate-500">{run.summary}</p>
            )}
          </div>
        )}
          </div>
        )}
      </div>

      {suppliers.length > 0 && (
        <ResultsPanel
          runId={runId}
          suppliers={suppliers}
          live={live}
          onNote={(id, note) => setSuppliers((prev) => prev.map((x) => (x.id === id ? { ...x, note } : x)))}
        />
      )}
      {!live && suppliers.length === 0 && run?.status !== "failed" && (
        <p className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">Подходящих поставщиков не найдено. Попробуйте другую категорию или локацию.</p>
      )}
    </section>
  );
}
