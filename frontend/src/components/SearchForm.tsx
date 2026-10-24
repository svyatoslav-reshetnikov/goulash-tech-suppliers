import { useState } from "react";
import { createRun } from "../api";
import CategoryPicker, { type PickedCategory } from "./CategoryPicker";
import LocationPicker, { type LocationChoice } from "./LocationPicker";

const TYPES = [
  { value: "", label: "Любой" },
  { value: "производитель", label: "Производитель" },
  { value: "дистрибьютор", label: "Дистрибьютор" },
  { value: "оптовик", label: "Оптовик" },
];

/** Форма поиска: категория, локация (город или регион, при желании с соседями), тип поставщика. */
export default function SearchForm({ onStarted, busy }: { onStarted: (runId: number) => void; busy: boolean }) {
  const [category, setCategory] = useState<PickedCategory | null>(null);
  const [location, setLocation] = useState<LocationChoice | null>(null);
  const [type, setType] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!category) return;
    setStarting(true);
    setError(null);
    try {
      const result = await createRun({
        category_id: category.id,
        location_kind: location?.selected.kind ?? null,
        location_key: location?.selected.key ?? null,
        include_neighbors: location?.includeNeighbors ?? false,
        supplier_type: type || null,
      });
      if ("error" in result) setError(result.error);
      else onStarted(result.runId);
    } catch {
      setError("Сервер не отвечает");
    } finally {
      setStarting(false);
    }
  };

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <CategoryPicker onChange={setCategory} />
      <LocationPicker onChange={setLocation} />
      <div>
        <label className="mb-1 block text-sm font-medium text-slate-600">Тип поставщика</label>
        <div className="flex flex-wrap gap-2">
          {TYPES.map((t) => (
            <button
              key={t.value}
              onClick={() => setType(t.value)}
              className={`rounded-full px-3 py-1 text-sm ${type === t.value ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={submit}
          disabled={!category || starting || busy}
          className="rounded-lg bg-slate-900 px-5 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {starting ? "Запускаем…" : "Найти поставщиков"}
        </button>
        <span className="text-xs text-slate-400">
          Поиск делает агент на платной модели (YandexGPT): обычно 40–70 ₽ за запуск, не больше ~250 ₽. Агент изучает сайты подробно, поэтому идёт 2–4 минуты.
        </span>
      </div>
      {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
    </section>
  );
}
