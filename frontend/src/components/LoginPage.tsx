import { useState, type FormEvent } from "react";
import { login, type AuthState } from "../api";

/** Страница входа: логин и пароль, ошибка под формой, пароль можно показать. */
export default function LoginPage({ onLoggedIn }: { onLoggedIn: (state: AuthState) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password) return;
    setBusy(true);
    setError(null);
    try {
      const result = await login(username, password);
      if ("error" in result) setError(result.error);
      else onLoggedIn(result);
    } catch {
      setError("Сервер не отвечает. Попробуйте ещё раз");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-gradient-to-b from-cyan-50 to-slate-50 px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <span className="rounded-full bg-cyan-100 px-3 py-1 text-xs font-medium text-cyan-800">тестовое задание</span>
          <h1 className="mt-4 text-2xl font-semibold text-slate-900">Поиск поставщиков продуктов питания</h1>
          <p className="mt-2 text-sm text-slate-500">Войдите, чтобы искать поставщиков и смотреть историю поисков</p>
        </div>

        <form onSubmit={submit} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div>
            <label htmlFor="username" className="mb-1 block text-sm font-medium text-slate-600">
              Логин
            </label>
            <input
              id="username"
              name="username"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100"
            />
          </div>
          <div>
            <label htmlFor="password" className="mb-1 block text-sm font-medium text-slate-600">
              Пароль
            </label>
            <div className="relative">
              <input
                id="password"
                name="password"
                type={show ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-slate-300 py-2 pl-3 pr-20 text-sm outline-none focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100"
              />
              <button
                type="button"
                onClick={() => setShow(!show)}
                className="absolute inset-y-0 right-0 px-3 text-xs text-slate-500 hover:text-slate-800"
                tabIndex={-1}
              >
                {show ? "Скрыть" : "Показать"}
              </button>
            </div>
          </div>
          {error && (
            <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={busy || !username.trim() || !password}
            className="w-full rounded-lg bg-slate-900 py-2.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {busy ? "Входим…" : "Войти"}
          </button>
        </form>
      </div>
    </main>
  );
}
