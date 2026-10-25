import { useEffect, useState } from "react";
import { createRun, createRuns, fetchLimits, fetchPreviousRun, type ExtrasInput, type ProductInput, type Run, type RunRequest } from "../api";
import { estimateText } from "../lib/estimate";
import ExtrasPanel, { isEmptyExtras } from "./ExtrasPanel";
import LocationPicker, { type LocationChoice } from "./LocationPicker";
import ProductPicker, { pickedName, type Picked } from "./ProductPicker";

const TYPES = [
  { value: "", label: "Любой" },
  { value: "производитель", label: "Производитель" },
  { value: "дистрибьютор", label: "Дистрибьютор" },
  { value: "оптовик", label: "Оптовик" },
];

const toProduct = (p: Picked): ProductInput =>
  p.kind === "catalog" ? { category_id: p.id, product_text: null } : { category_id: null, product_text: p.text };

type Props = {
  /** A search was launched: its run id and, for several products, the id of the group. */
  onStarted: (runId: number) => void;
  onOpenPrevious: (runId: number) => void;
  busy: boolean;
  initialProduct?: number | null;
};

type Confirmation = { products: Picked[]; duplicates: { product: Picked; run: Run }[] };

/** Форма поиска: товары (из справочника или свой текст), место, тип поставщика и необязательные требования. */
export default function SearchForm({ onStarted, onOpenPrevious, busy, initialProduct = null }: Props) {
  const [products, setProducts] = useState<Picked[]>([]);
  const [location, setLocation] = useState<LocationChoice | null>(null);
  const [type, setType] = useState("");
  const [extras, setExtras] = useState<ExtrasInput>({});
  const [maxProducts, setMaxProducts] = useState(3);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previous, setPrevious] = useState<Run | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);

  useEffect(() => {
    fetchLimits().then((l) => setMaxProducts(l.max_products)).catch(() => undefined);
  }, []);

  // the warning and the confirmation belong to one set of conditions: change it and they go away
  useEffect(() => {
    setPrevious(null);
    setConfirmation(null);
  }, [products, location, type, extras]);

  const common = () => ({
    location_kind: location?.selected.kind ?? null,
    location_key: location?.selected.key ?? null,
    include_neighbors: location?.includeNeighbors ?? false,
    supplier_type: type || null,
    extras: isEmptyExtras(extras) ? null : extras,
  });
  const requestFor = (p: Picked): RunRequest => ({ ...toProduct(p), ...common() });

  const launch = async (chosen: Picked[]) => {
    setStarting(true);
    setError(null);
    try {
      const result =
        chosen.length === 1 ? await createRun(requestFor(chosen[0])) : await createRuns({ ...common(), products: chosen.map(toProduct) });
      if ("error" in result) setError(result.error);
      else onStarted("runId" in result ? result.runId : result.runIds[0]);
    } catch {
      setError("Сервер не отвечает");
    } finally {
      setStarting(false);
      setPrevious(null);
      setConfirmation(null);
    }
  };

  const submit = async () => {
    if (products.length === 0) return;
    setStarting(true);
    setError(null);
    const found: (Run | null)[] = await Promise.all(products.map((p) => fetchPreviousRun(requestFor(p)).catch(() => null))); // a failed check never blocks a search
    setStarting(false);
    if (products.length === 1) {
      if (found[0]) setPrevious(found[0]);
      else await launch(products);
      return;
    }
    setConfirmation({ products, duplicates: products.flatMap((product, i) => (found[i] ? [{ product, run: found[i] as Run }] : [])) });
  };

  const fresh = confirmation ? confirmation.products.filter((p) => !confirmation.duplicates.some((d) => d.product === p)) : [];
  const blocked = previous !== null || confirmation !== null;

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <ProductPicker max={maxProducts} onChange={setProducts} initialCategoryId={initialProduct} />
      <LocationPicker onChange={setLocation} />
      <div>
        <label className="mb-1 block text-sm font-medium text-slate-700">Тип поставщика</label>
        <div className="flex flex-wrap gap-2">
          {TYPES.map((t) => (
            <button
              key={t.value}
              onClick={() => setType(t.value)}
              aria-pressed={type === t.value}
              className={`rounded-full px-3 py-1 text-sm ${type === t.value ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <ExtrasPanel value={extras} onChange={setExtras} />
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={submit}
          disabled={products.length === 0 || starting || busy || blocked}
          className="rounded-lg bg-slate-900 px-5 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {starting ? "Запускаем…" : products.length > 1 ? `Найти поставщиков (${products.length} товара)` : "Найти поставщиков"}
        </button>
        <span className="text-xs text-slate-600">
          Поиск делает агент на платной модели (YandexGPT): обычно 70–100 ₽ за запуск, не больше ~250 ₽. Агент изучает сайты подробно, поэтому идёт 2–4 минуты.
        </span>
      </div>

      {previous && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-semibold">Такой поиск уже осуществлялся</p>
          <p className="mt-1 text-amber-900">
            Поиск от {new Date(previous.created_at).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" })}
            {previous.status === "done" ? `, найдено поставщиков: ${previous.stats?.suppliers_saved ?? 0}` : ", ещё идёт"}. Новый поиск платный.
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
              onClick={() => void launch(products)}
              disabled={starting || busy}
              className="rounded-lg border border-slate-400 bg-white px-4 py-1.5 font-medium text-slate-800 hover:bg-slate-100 disabled:opacity-50"
            >
              Начать новый поиск
            </button>
          </div>
        </div>
      )}

      {confirmation && (
        <div role="alertdialog" aria-label="Подтверждение запуска" className="rounded-lg border border-cyan-300 bg-cyan-50 p-4 text-sm text-slate-900">
          <p className="font-semibold">{estimateText(confirmation.products.length)}</p>
          <p className="mt-1">Товары: {confirmation.products.map(pickedName).join(", ")}. Поиски идут по очереди, у каждого свой ход выполнения и своя стоимость.</p>
          {confirmation.duplicates.length > 0 && (
            <div className="mt-2 rounded bg-amber-50 p-2 text-amber-900">
              Уже искали: {confirmation.duplicates.map((d) => pickedName(d.product)).join(", ")}.{" "}
              {confirmation.duplicates.map((d) => (
                <button key={d.run.id} onClick={() => onOpenPrevious(d.run.id)} className="mr-2 underline">
                  Посмотреть «{pickedName(d.product)}»
                </button>
              ))}
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              onClick={() => void launch(confirmation.products)}
              disabled={starting || busy}
              className="rounded-lg bg-slate-900 px-4 py-1.5 font-medium text-white hover:bg-slate-700 disabled:opacity-50"
            >
              Запустить {confirmation.products.length}
            </button>
            {confirmation.duplicates.length > 0 && fresh.length > 0 && (
              <button
                onClick={() => void launch(fresh)}
                disabled={starting || busy}
                className="rounded-lg border border-slate-400 bg-white px-4 py-1.5 font-medium text-slate-800 hover:bg-slate-100 disabled:opacity-50"
              >
                Только новые ({fresh.length})
              </button>
            )}
            <button onClick={() => setConfirmation(null)} className="rounded-lg px-3 py-1.5 text-slate-700 hover:bg-slate-100">
              Отмена
            </button>
          </div>
        </div>
      )}
      {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
    </section>
  );
}
