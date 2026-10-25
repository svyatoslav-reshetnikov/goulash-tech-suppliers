import { useEffect, useMemo, useState } from "react";
import { fetchCategories, type CategoryGroup } from "../api";

/** Справочник категорий (ингредиентов), по которым ищем поставщиков. Нажмите на категорию, чтобы открыть форму поиска с этим товаром. */
export default function CategoriesTree() {
  const [groups, setGroups] = useState<CategoryGroup[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");

  useEffect(() => {
    fetchCategories()
      .then((data) => {
        setGroups(data.groups);
        setTotal(data.total);
      })
      .catch(() => setGroups([]));
  }, []);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    return groups
      .map((g) => ({
        ...g,
        categories: g.categories.filter(
          (c) => c.name.toLowerCase().includes(q) || c.synonyms.some((s) => s.toLowerCase().includes(q)),
        ),
      }))
      .filter((g) => g.categories.length > 0);
  }, [groups, query]);

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-slate-500">Категории для поиска поставщиков ({total})</h2>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Найти категорию"
          className="w-56 rounded-lg border border-slate-300 px-3 py-1.5 text-sm outline-none focus:border-cyan-500"
        />
      </div>
      <p className="mb-3 text-sm text-slate-600">Нажмите на категорию: откроется форма поиска с этим товаром.</p>
      <div className="space-y-2">
        {shown.map((group) => (
          <details key={group.id} open={query.trim() !== ""} className="rounded-lg border border-slate-100">
            <summary className="flex cursor-pointer items-center justify-between px-3 py-2 text-sm font-medium">
              <span>
                {group.name}
                {group.kind === "packaging" && (
                  <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-normal text-amber-800">
                    упаковка
                  </span>
                )}
              </span>
              <span className="text-xs font-normal text-slate-400">{group.categories.length}</span>
            </summary>
            <ul className="flex flex-wrap gap-2 px-3 pb-3">
              {group.categories.map((c) => (
                <li key={c.id}>
                  <a
                    href={`#search?product=${c.id}`}
                    title={`Искать поставщиков: ${c.name}${c.synonyms.length ? `. Синонимы: ${c.synonyms.join(", ")}` : ""}`}
                    className="block rounded-full bg-slate-100 px-3 py-1 text-sm text-slate-700 hover:bg-cyan-100 hover:text-cyan-900"
                  >
                    {c.name}
                    {c.items > 0 && <span className="ml-1.5 text-xs text-slate-500">{c.items}</span>}
                  </a>
                </li>
              ))}
            </ul>
          </details>
        ))}
        {shown.length === 0 && <p className="text-sm text-slate-500">Категорий не найдено</p>}
      </div>
    </section>
  );
}
