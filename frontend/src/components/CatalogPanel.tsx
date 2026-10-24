import { useEffect, useState } from "react";
import { fetchCatalogStatus, type CatalogStatus } from "../api";

/** Справочник зафиксирован (загружается из снимка при первом запуске); обновления из приложения нет. */
export default function CatalogPanel({ onStatus }: { onStatus: (status: CatalogStatus) => void }) {
  const [status, setStatus] = useState<CatalogStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetchCatalogStatus()
      .then((next) => {
        setStatus(next);
        onStatus(next);
      })
      .catch(() => setError(true));
  }, [onStatus]);

  if (error) {
    return <section className="rounded-xl border border-red-200 bg-red-50 p-5 text-red-700">Сервер не отвечает</section>;
  }
  if (!status) return <section className="p-5 text-slate-500">Загрузка…</section>;
  if (status.state !== "ready") {
    return (
      <section className="rounded-xl border border-amber-200 bg-amber-50 p-5 text-amber-800">
        Справочник категорий пуст. Проверьте, что файл снимка <code>seed/catalog_snapshot.json</code> есть в образе.
      </section>
    );
  }
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-semibold">
        Справочник: {status.categories_active} категорий
        <span className="ml-2 text-sm font-normal text-slate-400">из {status.active_items} позиций меню</span>
      </h2>
      <p className="text-sm text-slate-500">
        Составлен по меню «Сушкофа» (eda1.ru) и зафиксирован: для тестового задания он не обновляется.
      </p>
    </section>
  );
}
