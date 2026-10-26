import { useEffect, useMemo, useRef, useState } from "react";
import { fetchCategories } from "../api";

/** A catalog category: the search runs strictly by the catalog, one product at a time. */
export type Picked = { id: number; name: string; group: string };

const fold = (s: string) => s.toLowerCase().replace(/ё/g, "е");

type Option = Picked & { names: string[] };

type Props = {
  onChange: (product: Picked | null) => void;
  /** Catalog category to select once the catalog is loaded (e.g. a chip clicked in «Справочник»). */
  initialCategoryId?: number | null;
};

/** «Что закупаем»: one product from the catalog, found by name or synonym. */
export default function ProductPicker({ onChange, initialCategoryId = null }: Props) {
  const [all, setAll] = useState<Option[] | null>(null); // null: справочник ещё загружается
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [product, setProduct] = useState<Picked | null>(null);
  const [failed, setFailed] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const [prefilled, setPrefilled] = useState(initialCategoryId === null);

  useEffect(() => {
    fetchCategories()
      .then((data) =>
        setAll(data.groups.flatMap((g) => g.categories.map((c) => ({ id: c.id, name: c.name, group: g.name, names: [c.name, ...c.synonyms].map(fold) })))),
      )
      .catch(() => {
        setAll([]);
        setFailed(true);
      });
  }, []);

  useEffect(() => {
    onChange(product);
  }, [product]); // eslint-disable-line react-hooks/exhaustive-deps

  // a chip in the catalog opens the form with its product already chosen
  useEffect(() => {
    if (prefilled || initialCategoryId === null || all === null) return;
    const found = all.find((c) => c.id === initialCategoryId);
    if (found) setProduct({ id: found.id, name: found.name, group: found.group });
    setPrefilled(true);
  }, [all, initialCategoryId, prefilled]);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const q = fold(query.trim());
  const browsing = q === "";
  const options = useMemo(() => {
    if (all === null) return [];
    if (browsing) return all; // the field was just opened: show every category grouped by its group
    return all
      .map((c) => ({ c, rank: c.names.some((n) => n === q) ? 0 : c.names.some((n) => n.startsWith(q)) ? 1 : c.names.some((n) => n.includes(q)) ? 2 : 9 }))
      .filter((x) => x.rank < 9)
      .sort((a, b) => a.rank - b.rank || a.c.name.localeCompare(b.c.name, "ru"))
      .slice(0, 20)
      .map((x) => x.c);
  }, [all, q, browsing]);

  // товар пришёл из ссылки справочника: пока он не подставлен, вместо пустого поля видна загрузка
  const prefilling = !prefilled;

  const choose = (c: Option) => {
    setProduct({ id: c.id, name: c.name, group: c.group });
    setQuery("");
    setOpen(false);
  };

  return (
    <div ref={box} className="relative">
      <label htmlFor="product-input" className="mb-1 block text-sm font-medium text-slate-700">
        Что закупаем
      </label>
      {prefilling ? (
        <p className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-500">Загружаем справочник…</p>
      ) : product ? (
        <div className="flex items-center gap-1 self-start">
          <span className="flex items-center gap-1 rounded-full bg-cyan-100 py-1 pl-3 pr-1 text-sm text-cyan-900">
            {product.name}
            <span className="text-xs text-cyan-800">{product.group}</span>
            <button onClick={() => setProduct(null)} aria-label={`Убрать: ${product.name}`} className="ml-1 rounded-full px-2 text-cyan-900 hover:bg-cyan-200">
              ×
            </button>
          </span>
        </div>
      ) : (
        <>
          <input
            id="product-input"
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
            placeholder="Выберите из справочника или начните вводить: моцарелла, нори, лосось"
            role="combobox"
            aria-expanded={open}
            aria-controls="product-options"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-cyan-500"
          />
          {failed && <p className="mt-1 text-xs text-red-600">Не удалось загрузить справочник категорий</p>}
          {open && (
            <ul id="product-options" className="absolute z-10 mt-1 max-h-80 w-full overflow-auto rounded-lg border border-slate-200 bg-white shadow-lg">
              {options.map((c, i) => (
                <li key={c.id}>
                  {browsing && (i === 0 || options[i - 1].group !== c.group) && (
                    <div className="sticky top-0 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600">{c.group}</div>
                  )}
                  <button onClick={() => choose(c)} className="flex w-full items-baseline justify-between px-3 py-2 text-left text-sm hover:bg-slate-50">
                    <span className="font-medium">{c.name}</span>
                    {!browsing && <span className="text-xs text-slate-500">{c.group}</span>}
                  </button>
                </li>
              ))}
              {options.length === 0 && (
                <li className="px-3 py-2 text-sm text-slate-500">
                  {all === null ? "Загружаем справочник…" : failed ? "Справочник недоступен" : "В справочнике такой позиции нет"}
                </li>
              )}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
