import type { SupplierData } from "../api";

const ROWS: [string, string][] = [
  ["supplier_type", "Тип"],
  ["phone", "Телефон"],
  ["email", "Email"],
  ["address", "Адрес"],
  ["description", "Чем занимается"],
  ["assortment", "Ассортимент"],
  ["regions", "Регионы поставки"],
  ["delivery_terms", "Доставка"],
  ["moq", "Минимальный заказ"],
  ["price_hint", "Цены"],
  ["certificates", "Сертификаты"],
];

/** Сравнение 2–4 поставщиков бок о бок; недостающие данные видны сразу. */
export default function ComparisonTable({ suppliers, onClose }: { suppliers: SupplierData[]; onClose: () => void }) {
  const best = Math.max(...suppliers.map((s) => s.ranking.score));
  const cell = (s: SupplierData, field: string) => {
    const values = s.fields[field]?.map((v) => v.value) ?? [];
    return values.length ? values.join("; ") : null;
  };
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">Сравнение ({suppliers.length})</h2>
        <button onClick={onClose} className="text-sm text-slate-500 hover:text-slate-800">
          Закрыть
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] border-collapse text-left text-sm">
          <thead>
            <tr>
              <th className="sticky left-0 w-32 bg-white p-2 text-xs font-normal text-slate-400" />
              {suppliers.map((s) => (
                <th key={s.id} className="p-2 align-bottom">
                  <div className="font-semibold">{s.name}</div>
                  <a href={s.website} target="_blank" rel="noreferrer" className="text-xs font-normal text-cyan-700 hover:underline">
                    {s.domain}
                  </a>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="align-top">
            <tr className="border-t border-slate-100">
              <th className="sticky left-0 bg-white p-2 text-xs font-normal text-slate-400">Оценка</th>
              {suppliers.map((s) => (
                <td key={s.id} className="p-2">
                  <span className={`font-semibold ${s.ranking.score === best ? "text-emerald-700" : ""}`}>{s.ranking.score}</span>
                  <span className="text-xs text-slate-400"> из 100 · место {s.ranking.rank}</span>
                </td>
              ))}
            </tr>
            <tr className="border-t border-slate-100">
              <th className="sticky left-0 bg-white p-2 text-xs font-normal text-slate-400">Расположение</th>
              {suppliers.map((s) => (
                <td key={s.id} className="p-2">{s.ranking.tier_label}</td>
              ))}
            </tr>
            {ROWS.map(([field, label]) => (
              <tr key={field} className="border-t border-slate-100">
                <th className="sticky left-0 bg-white p-2 text-xs font-normal text-slate-400">{label}</th>
                {suppliers.map((s) => {
                  const value = cell(s, field);
                  return (
                    <td key={s.id} className="p-2">
                      {value ?? <span className="italic text-slate-300">нет данных</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
            <tr className="border-t border-slate-100">
              <th className="sticky left-0 bg-white p-2 text-xs font-normal text-slate-400">Заметка</th>
              {suppliers.map((s) => (
                <td key={s.id} className="p-2">{s.note ?? <span className="italic text-slate-300">нет заметки</span>}</td>
              ))}
            </tr>
            <tr className="border-t border-slate-100">
              <th className="sticky left-0 bg-white p-2 text-xs font-normal text-slate-400">ИНН</th>
              {suppliers.map((s) => (
                <td key={s.id} className="p-2">{s.inn ?? <span className="italic text-slate-300">нет данных</span>}</td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}
