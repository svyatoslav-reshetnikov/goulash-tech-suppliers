import { useEffect, useMemo, useRef, useState } from "react";
import { fetchCategories } from "../api";

/** A catalog category or a free-text product (not in the catalog). */
export type Picked = { kind: "catalog"; id: number; name: string; group: string } | { kind: "free"; text: string };
export const pickedName = (p: Picked): string => (p.kind === "catalog" ? p.name : p.text);
export const pickedKey = (p: Picked): string => (p.kind === "catalog" ? `c${p.id}` : `t${p.text.toLowerCase()}`);

const fold = (s: string) => s.toLowerCase().replace(/ё/g, "е");
const FREE_MIN = 2;
const FREE_MAX = 80;

type Option = { id: number; name: string; group: string; names: string[] };

type Props = {
  max: number;
  onChange: (products: Picked[]) => void;
  /** Catalog category to select once the catalog is loaded (e.g. a chip clicked in «Справочник»). */
  initialCategoryId?: number | null;
};

/**
 * «Что закупаем»: one or several products as tags. Choose from the catalog (name or synonym search) or, when nothing fits,
 * search for a free-text product "as is".
 */
export default function ProductPicker({ max, onChange, initialCategoryId = null }: Props) {
  const [all, setAll] = useState<Option[]>([]);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [products, setProducts] = useState<Picked[]>([]);
  const [failed, setFailed] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const prefilled = useRef(false);

  useEffect(() => {
    fetchCategories()
      .then((data) =>
        setAll(data.groups.flatMap((g) => g.categories.map((c) => ({ id: c.id, name: c.name, group: g.name, names: [c.name, ...c.synonyms].map(fold) })))),
      )
      .catch(() => setFailed(true));
  }, []);

  useEffect(() => {
    onChange(products);
  }, [products]); // eslint-disable-line react-hooks/exhaustive-deps

  // a chip in the catalog opens the form with its product already chosen
  useEffect(() => {
    if (prefilled.current || initialCategoryId === null || all.length === 0) return;
    const found = all.find((c) => c.id === initialCategoryId);
    if (found) setProducts([{ kind: "catalog", id: found.id, name: found.name, group: found.group }]);
    prefilled.current = true;
  }, [all, initialCategoryId]);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const chosen = useMemo(() => new Set(products.map(pickedKey)), [products]);
  const q = fold(query.trim());
  const browsing = q === "";
  const options = useMemo(() => {
    const free = all.filter((c) => !chosen.has(`c${c.id}`));
    if (browsing) return free; // the field was just opened: show every category grouped by its group
    return free
      .map((c) => ({ c, rank: c.names.some((n) => n === q) ? 0 : c.names.some((n) => n.startsWith(q)) ? 1 : c.names.some((n) => n.includes(q)) ? 2 : 9 }))
      .filter((x) => x.rank < 9)
      .sort((a, b) => a.rank - b.rank || a.c.name.localeCompare(b.c.name, "ru"))
      .slice(0, 20)
      .map((x) => x.c);
  }, [all, chosen, q, browsing]);

  const full = products.length >= max;
  const exact = all.some((c) => c.names.includes(q));
  const freeText = query.trim().replace(/\s+/g, " ");
  const canFree = !browsing && !exact && freeText.length >= FREE_MIN && freeText.length <= FREE_MAX && !chosen.has(pickedKey({ kind: "free", text: freeText }));

  const add = (product: Picked) => {
    if (products.length >= max) return;
    setProducts((prev) => [...prev, product]);
    setQuery("");
    setOpen(false);
  };

  return (
    <div ref={box} className="relative">
      <label htmlFor="product-input" className="mb-1 block text-sm font-medium text-slate-700">
        Что закупаем
        {max > 1 && <span className="ml-2 text-xs font-normal text-slate-500">можно до {max} товаров сразу</span>}
      </label>
      {products.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-2" aria-label="Выбранные товары">
          {products.map((p) => (
            <li key={pickedKey(p)} className="flex items-center gap-1 rounded-full bg-cyan-100 py-1 pl-3 pr-1 text-sm text-cyan-900">
              {pickedName(p)}
              <span className="text-xs text-cyan-800">{p.kind === "catalog" ? p.group : "не из справочника"}</span>
              <button
                onClick={() => setProducts((prev) => prev.filter((x) => pickedKey(x) !== pickedKey(p)))}
                aria-label={`Убрать: ${pickedName(p)}`}
                className="ml-1 rounded-full px-2 text-cyan-900 hover:bg-cyan-200"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {full ? (
        <p className="text-xs text-slate-600">Выбрано максимум товаров ({max}). Уберите один, чтобы добавить другой.</p>
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
              if (e.key === "Enter" && !browsing) {
                if (options[0]) add({ kind: "catalog", id: options[0].id, name: options[0].name, group: options[0].group });
                else if (canFree) add({ kind: "free", text: freeText });
              }
              if (e.key === "Escape") setOpen(false);
            }}
            placeholder={products.length ? "Добавить ещё товар" : "Выберите из списка или начните вводить: моцарелла, нори, лосось"}
            role="combobox"
            aria-expanded={open}
            aria-controls="product-options"
            maxLength={FREE_MAX + 20}
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
                  <button onClick={() => add({ kind: "catalog", id: c.id, name: c.name, group: c.group })} className="flex w-full items-baseline justify-between px-3 py-2 text-left text-sm hover:bg-slate-50">
                    <span className="font-medium">{c.name}</span>
                    {!browsing && <span className="text-xs text-slate-500">{c.group}</span>}
                  </button>
                </li>
              ))}
              {canFree && (
                <li className="border-t border-slate-100">
                  <button onClick={() => add({ kind: "free", text: freeText })} className="w-full px-3 py-2 text-left text-sm text-cyan-800 hover:bg-cyan-50">
                    Такой позиции нет в справочнике — искать как есть: «{freeText}»
                  </button>
                </li>
              )}
              {options.length === 0 && !canFree && (
                <li className="px-3 py-2 text-sm text-slate-500">
                  {failed ? "Справочник недоступен" : browsing ? "Все категории уже выбраны" : freeText.length < FREE_MIN ? "Введите хотя бы 2 буквы" : `Название длиннее ${FREE_MAX} символов`}
                </li>
              )}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
