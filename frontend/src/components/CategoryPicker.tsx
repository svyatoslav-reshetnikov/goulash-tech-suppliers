import { useEffect, useMemo, useRef, useState } from "react";
import { fetchCategories } from "../api";

export type PickedCategory = { id: number; name: string; group: string };

const fold = (s: string) => s.toLowerCase().replace(/ё/g, "е");

/** Выбор категории (продукта) из справочника: поиск по названию и синонимам. */
export default function CategoryPicker({ onChange }: { onChange: (category: PickedCategory | null) => void }) {
  const [all, setAll] = useState<(PickedCategory & { names: string[] })[]>([]);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<PickedCategory | null>(null);
  const [failed, setFailed] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchCategories()
      .then((data) =>
        setAll(
          data.groups.flatMap((g) =>
            g.categories.map((c) => ({ id: c.id, name: c.name, group: g.name, names: [c.name, ...c.synonyms].map(fold) })),
          ),
        ),
      )
      .catch(() => setFailed(true));
  }, []);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const options = useMemo(() => {
    const q = fold(query.trim());
    if (!q) return all; // поле только что открыли: показываем все категории, сгруппированные по группам
    const scored = all
      .map((c) => ({ c, rank: c.names.some((n) => n === q) ? 0 : c.names.some((n) => n.startsWith(q)) ? 1 : c.names.some((n) => n.includes(q)) ? 2 : 9 }))
      .filter((x) => x.rank < 9)
      .sort((a, b) => a.rank - b.rank || a.c.name.localeCompare(b.c.name, "ru"));
    return scored.slice(0, 20).map((x) => x.c);
  }, [all, query]);

  const browsing = query.trim() === "";

  const choose = (category: PickedCategory) => {
    setSelected(category);
    onChange(category);
    setQuery("");
    setOpen(false);
  };

  return (
    <div ref={box} className="relative">
      <label className="mb-1 block text-sm font-medium text-slate-600">Что закупаем</label>
      {selected ? (
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-cyan-100 px-3 py-1 text-sm text-cyan-900">
            {selected.name}
            <span className="ml-2 text-xs text-cyan-700">{selected.group}</span>
          </span>
          <button
            onClick={() => {
              setSelected(null);
              onChange(null);
            }}
            className="text-sm text-slate-500 hover:text-slate-800"
          >
            Изменить
          </button>
        </div>
      ) : (
        <>
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !browsing && options[0]) choose(options[0]);
              if (e.key === "Escape") setOpen(false);
            }}
            placeholder="Выберите из списка или начните вводить: моцарелла, нори, лосось"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-cyan-500"
          />
          {failed && <p className="mt-1 text-xs text-red-600">Не удалось загрузить справочник категорий</p>}
          {open && (
            <ul className="absolute z-10 mt-1 max-h-80 w-full overflow-auto rounded-lg border border-slate-200 bg-white shadow-lg">
              {options.map((c, i) => (
                <li key={c.id}>
                  {browsing && (i === 0 || options[i - 1].group !== c.group) && (
                    <div className="sticky top-0 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-500">{c.group}</div>
                  )}
                  <button onClick={() => choose(c)} className="flex w-full items-baseline justify-between px-3 py-2 text-left text-sm hover:bg-slate-50">
                    <span className="font-medium">{c.name}</span>
                    {!browsing && <span className="text-xs text-slate-400">{c.group}</span>}
                  </button>
                </li>
              ))}
              {options.length === 0 && (
                <li className="px-3 py-2 text-sm text-slate-400">{failed ? "Справочник недоступен" : "Такой категории нет в справочнике"}</li>
              )}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
