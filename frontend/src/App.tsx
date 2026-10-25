import { useCallback, useEffect, useState } from "react";
import { UNAUTHORIZED_EVENT, fetchAuth, fetchHealth, logout, type AuthState, type Health } from "./api";
import CatalogPage from "./components/CatalogPage";
import HistoryPage from "./components/HistoryPage";
import LoginPage from "./components/LoginPage";
import { parseRoute } from "./route";
import SearchPage from "./components/SearchPage";


export default function App() {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  const [health, setHealth] = useState<Health | "unreachable" | null>(null);
  const [auth, setAuth] = useState<AuthState | null>(null);

  useEffect(() => {
    const onHash = () => setRoute(parseRoute(window.location.hash));
    // сессия закончилась посреди работы: любой ответ 401 возвращает на страницу входа
    const onUnauthorized = () => setAuth((prev) => (prev ? { ...prev, authenticated: false } : prev));
    window.addEventListener("hashchange", onHash);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    fetchHealth().then(setHealth).catch(() => setHealth("unreachable"));
    fetchAuth().then(setAuth).catch(() => setAuth({ auth_required: false, authenticated: true, user: null }));
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    };
  }, []);

  const go = useCallback((hash: string) => {
    window.location.hash = hash;
  }, []);

  const tabClass = (active: boolean) =>
    `rounded-lg px-4 py-1.5 text-sm font-medium ${active ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`;

  if (auth === null) return <div className="p-8 text-sm text-slate-400">Загрузка…</div>;
  if (!auth.authenticated) return <LoginPage onLoggedIn={setAuth} />;

  return (
    <div className="mx-auto max-w-[1100px] space-y-5 px-4 py-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Поиск поставщиков продуктов питания</h1>
        <span className="flex items-center gap-3">
          <span className="rounded-full bg-cyan-100 px-3 py-1 text-xs font-medium text-cyan-800">тестовое задание</span>
          {auth.auth_required && (
            <button
              onClick={() => {
                void logout().then(() => setAuth({ auth_required: true, authenticated: false, user: null }));
              }}
              className="text-sm text-slate-500 hover:text-slate-800"
              title={auth.user ? `Вы вошли как ${auth.user}` : undefined}
            >
              Выйти
            </button>
          )}
        </span>
      </header>

      <nav className="flex gap-2">
        <button onClick={() => go("search")} className={tabClass(route.tab === "search")}>Найти поставщика</button>
        <button onClick={() => go("history")} className={tabClass(route.tab === "history")}>История поисков</button>
        <button onClick={() => go("catalog")} className={tabClass(route.tab === "catalog")}>Справочник</button>
      </nav>

      {route.tab === "search" && <SearchPage runId={route.runId} initialProduct={route.product} onOpen={(id) => go(`search/${id}`)} />}
      {route.tab === "history" && <HistoryPage runId={route.runId} onOpen={(id) => go(`history/${id}`)} onBack={() => go("history")} />}
      {route.tab === "catalog" && <CatalogPage />}

      <footer className="text-xs text-slate-400">
        {health === null && "Проверяем сервис…"}
        {health === "unreachable" && <span className="text-red-600">Сервер не отвечает</span>}
        {health && health !== "unreachable" && `Сервер: ${health.status} · база данных: ${health.database}`}
      </footer>
    </div>
  );
}
