import { useEffect, useState } from "react";
import { createRun, fetchPreviousRun, type Run, type RunRequest } from "../api";
import LocationPicker, { type LocationChoice } from "./LocationPicker";
import ProductPicker, { type Picked } from "./ProductPicker";

type Props = {
  onStarted: (runId: number) => void;
  onOpenPrevious: (runId: number) => void;
  busy: boolean;
  initialProduct?: number | null;
};

/** Форма поиска: один товар из справочника и место. */
export default function SearchForm({ onStarted, onOpenPrevious, busy, initialProduct = null }: Props) {
  const [product, setProduct] = useState<Picked | null>(null);
  const [location, setLocation] = useState<LocationChoice | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previous, setPrevious] = useState<Run | null>(null);

  // the warning belongs to one set of conditions: change it and it goes away
  useEffect(() => setPrevious(null), [product, location]);

  const request = (p: Picked): RunRequest => ({
    category_id: p.id,
    location_kind: location?.selected.kind ?? null,
    location_key: location?.selected.key ?? null,
    include_neighbors: location?.includeNeighbors ?? false,
  });

  const launch = async () => {
    if (!product) return;
    setStarting(true);
    setError(null);
    try {
      const result = await createRun(request(product));
      if ("error" in result) setError(result.error);
      else onStarted(result.runId);
    } catch {
      setError("Сервер не отвечает");
    } finally {
      setStarting(false);
      setPrevious(null);
    }
  };

  const submit = async () => {
    if (!product) return;
    setStarting(true);
    setError(null);
    const found = await fetchPreviousRun(request(product)).catch(() => null); // a failed check never blocks a search
    setStarting(false);
    if (found) setPrevious(found);
    else await launch();
  };

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <ProductPicker onChange={setProduct} initialCategoryId={initialProduct} />
      <LocationPicker onChange={setLocation} />
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={submit}
          disabled={!product || starting || busy || previous !== null}
          className="rounded-lg bg-slate-900 px-5 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {starting ? "Запускаем…" : "Найти поставщиков"}
        </button>
        <span className="text-xs text-slate-600">Агент изучает сайты поставщиков подробно, поэтому поиск идёт 2–4 минуты.</span>
      </div>

      {previous && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-semibold">Такой поиск уже осуществлялся</p>
          <p className="mt-1 text-amber-900">
            Поиск от {new Date(previous.created_at).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" })}
            {previous.status === "done" ? `, найдено поставщиков: ${previous.stats?.suppliers_saved ?? 0}` : ", ещё идёт"}.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              onClick={() => {
                setPrevious(null);
                onOpenPrevious(previous.id);
              }}
              className="rounded-lg bg-slate-900 px-4 py-1.5 font-medium text-white hover:bg-slate-700"
            >
              Посмотреть результаты предыдущего поиска
            </button>
            <button
              onClick={() => void launch()}
              disabled={starting || busy}
              className="rounded-lg border border-slate-400 bg-white px-4 py-1.5 font-medium text-slate-800 hover:bg-slate-100 disabled:opacity-50"
            >
              Начать новый поиск
            </button>
          </div>
        </div>
      )}
      {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
    </section>
  );
}
