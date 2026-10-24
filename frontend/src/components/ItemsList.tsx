import { useEffect, useState } from "react";
import { fetchCatalogItems, type CatalogItem } from "../api";

const PAGE = 30;

/** Просмотр скачанного каталога: поиск по названию и описанию. На этапе 3 здесь появятся категории-ингредиенты. */
export default function ItemsList() {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      setLoading(true);
      fetchCatalogItems(query, 0, PAGE)
        .then((data) => {
          setItems(data.items);
          setTotal(data.total);
        })
        .catch(() => setItems([]))
        .finally(() => setLoading(false));
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);

  const more = async () => {
    const data = await fetchCatalogItems(query, items.length, PAGE);
    setItems((prev) => [...prev, ...data.items]);
  };

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-slate-500">Позиции каталога</h2>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск по названию и описанию"
          className="w-72 rounded-lg border border-slate-300 px-3 py-1.5 text-sm outline-none focus:border-cyan-500"
        />
      </div>
      <ul className="divide-y divide-slate-100">
        {items.map((item) => (
          <li key={item.source_id} className="py-2.5">
            <div className="flex items-baseline justify-between gap-3">
              <span className="font-medium">{item.name}</span>
              <span className="shrink-0 text-xs text-slate-400">{item.weight}</span>
            </div>
            {item.description && <p className="text-sm text-slate-600">{item.description}</p>}
            {item.sections.length > 0 && (
              <p className="mt-0.5 text-xs text-slate-400">{item.sections.join(" · ")}</p>
            )}
          </li>
        ))}
      </ul>
      {!loading && items.length === 0 && <p className="text-sm text-slate-500">Ничего не найдено</p>}
      {items.length < total && (
        <button onClick={more} className="mt-3 text-sm text-cyan-700 hover:underline">
          Показать ещё ({total - items.length})
        </button>
      )}
    </section>
  );
}
