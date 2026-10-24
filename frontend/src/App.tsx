import { useCallback, useEffect, useState } from "react";
import { fetchHealth, type Health } from "./api";
import CatalogPage from "./components/CatalogPage";
import SearchPage from "./components/SearchPage";

type Route = { tab: "search" | "catalog"; runId: number | null };

/** Адрес вида #search, #search/3 (открыт запуск 3) или #catalog: ссылку на запуск можно сохранить и открыть позже. */
const parse = (hash: string): Route => {
  const [tab, id] = hash.replace(/^#/, "").split("/");
  return { tab: tab === "catalog" ? "catalog" : "search", runId: id && /^\d+$/.test(id) ? Number(id) : null };
};

export default function App() {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  const [health, setHealth] = useState<Health | "unreachable" | null>(null);

  useEffect(() => {
    const onHash = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", onHash);
    fetchHealth().then(setHealth).catch(() => setHealth("unreachable"));
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = useCallback((hash: string) => {
    window.location.hash = hash;
  }, []);

  const tabClass = (active: boolean) =>
    `rounded-lg px-4 py-1.5 text-sm font-medium ${active ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`;

  return (
    <div className="mx-auto max-w-3xl space-y-5 px-4 py-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Поиск поставщиков продуктов питания</h1>
        <span className="rounded-full bg-cyan-100 px-3 py-1 text-xs font-medium text-cyan-800">тестовое задание</span>
      </header>

      <nav className="flex gap-2">
        <button onClick={() => go("search")} className={tabClass(route.tab === "search")}>Поиск</button>
        <button onClick={() => go("catalog")} className={tabClass(route.tab === "catalog")}>Справочник</button>
      </nav>

      {route.tab === "search" ? (
        <SearchPage runId={route.runId} onOpen={(id) => go(`search/${id}`)} />
      ) : (
        <CatalogPage />
      )}

      <footer className="text-xs text-slate-400">
        {health === null && "Проверяем сервис…"}
        {health === "unreachable" && <span className="text-red-600">Сервер не отвечает</span>}
        {health && health !== "unreachable" && `Сервер: ${health.status} · база данных: ${health.database}`}
      </footer>
    </div>
  );
}
