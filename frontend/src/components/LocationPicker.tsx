import { useEffect, useRef, useState } from "react";
import { resolveLocation, searchLocations, type LocationMatch, type ResolvedLocation } from "../api";

const formatPopulation = (n: number | null) =>
  n === null ? "" : n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1).replace(".", ",")} млн` : `${Math.round(n / 1000)} тыс.`;

/** Где искать поставщиков: город или регион России (или вся Россия) и переключатель «включая соседние регионы». */
export type LocationChoice = { selected: LocationMatch; includeNeighbors: boolean };

export default function LocationPicker({ onChange }: { onChange?: (value: LocationChoice | null) => void }) {
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<LocationMatch[]>([]);
  const [searching, setSearching] = useState(false); // ответ на последний ввод ещё не пришёл: «Ничего не найдено» пока не показываем
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<LocationMatch | null>(null);
  const [withNeighbors, setWithNeighbors] = useState(false);
  // регион и все его соседи, загруженные сразу при выборе: галочка «соседние» только показывает их, без нового запроса,
  // поэтому между щелчком и ответом сервера не мелькает «соседних регионов нет»
  const [resolved, setResolved] = useState<{ key: string; data: ResolvedLocation } | null>(null);
  const [error, setError] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // автодополнение с задержкой, чтобы не отправлять запрос на каждую букву
  useEffect(() => {
    if (!query.trim()) {
      setOptions([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    let stale = false; // ответ на прежний ввод, пришедший позже нового, не показываем
    const timer = setTimeout(() => {
      searchLocations(query)
        .then((found) => {
          if (stale) return;
          setOptions(found);
          setError(false);
        })
        .catch(() => !stale && setError(true))
        .finally(() => !stale && setSearching(false));
    }, 200);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [query]);

  // что выбрано и в каких регионах искать
  useEffect(() => {
    onChange?.(selected ? { selected, includeNeighbors: withNeighbors } : null);
  }, [selected, withNeighbors]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selected) {
      setResolved(null);
      return;
    }
    let stale = false;
    const key = `${selected.kind}:${selected.key}`;
    resolveLocation(selected, true)
      .then((data) => !stale && setResolved({ key, data }))
      .catch(() => !stale && setError(true));
    return () => {
      stale = true;
    };
  }, [selected]);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const choose = (match: LocationMatch) => {
    setSelected(match);
    setQuery("");
    setOptions([]);
    setOpen(false);
  };

  const current = selected && resolved?.key === `${selected.kind}:${selected.key}` ? resolved.data : null; // не ответ для прежнего выбора
  const neighbors = current?.regions.filter((r) => r.role === "neighbor") ?? [];
  const primary = current?.regions.find((r) => r.role === "primary");

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="mb-3 text-sm font-medium text-slate-700">Где искать поставщиков</h2>

      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => setSelected(null)}
          aria-pressed={!selected}
          className={`rounded-full px-3 py-1 text-sm ${!selected ? "bg-cyan-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
        >
          Вся Россия
        </button>
        {selected && (
          <span className="rounded-full bg-cyan-100 px-3 py-1 text-sm text-cyan-900">
            {selected.kind === "city" ? `${selected.label} · ${selected.region_name}` : selected.label}
          </span>
        )}
      </div>

      <div ref={box} className="relative mt-3">
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setOpen(false);
            if (e.key === "Enter") {
              if (options[0]) choose(options[0]);
              else if (query.trim()) void searchLocations(query).then((found) => found[0] && choose(found[0]));
            }
          }}
          placeholder="Город или регион: например, Екатеринбург, Татарстан, Питер"
          role="combobox"
          aria-expanded={open}
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-cyan-500"
        />
        {open && query.trim() !== "" && (
          <ul className="absolute z-10 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-slate-200 bg-white shadow-lg">
            {options.map((o) => (
              <li key={`${o.kind}:${o.key}`}>
                <button
                  onClick={() => choose(o)}
                  className="flex w-full items-baseline justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-slate-50"
                >
                  <span>
                    <span className="font-medium">{o.label}</span>
                    <span className="ml-2 text-xs text-slate-400">
                      {o.kind === "region" ? "регион" : o.region_name}
                    </span>
                  </span>
                  <span className="shrink-0 text-xs text-slate-400">{formatPopulation(o.population)}</span>
                </button>
              </li>
            ))}
            {options.length === 0 && !error && (
              <li className="px-3 py-2 text-sm text-slate-400">{searching ? "Ищем…" : "Ничего не найдено"}</li>
            )}
            {error && <li className="px-3 py-2 text-sm text-red-600">Не удалось выполнить поиск</li>}
          </ul>
        )}
      </div>

      <label className={`mt-4 flex items-center gap-2 text-sm ${selected ? "text-slate-800" : "text-slate-500"}`}>
        <input
          type="checkbox"
          checked={withNeighbors}
          disabled={!selected}
          aria-describedby={selected ? undefined : "neighbors-hint"}
          onChange={(e) => setWithNeighbors(e.target.checked)}
        />
        Включая соседние регионы
      </label>
      {!selected && (
        <p id="neighbors-hint" className="mt-1 text-xs text-slate-600">
          Сначала выберите регион
        </p>
      )}

      {selected && !primary && withNeighbors && <p className="mt-3 text-sm text-slate-500">Загружаем соседние регионы…</p>}
      {primary && (
        <div className="mt-3 text-sm">
          <p className="text-slate-600">
            Регион поиска: <b>{primary.name}</b>
            {withNeighbors && (neighbors.length > 0 ? ` и соседние (${neighbors.length})` : ": соседних регионов нет")}
          </p>
          {withNeighbors && neighbors.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-2">
              {neighbors.map((r) => (
                <li key={r.id} className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-700">
                  {r.name}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
