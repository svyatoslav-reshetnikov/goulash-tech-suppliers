import type { SupplierData } from "../api";
import { FieldText } from "./Linkified";

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
export default function ComparisonTable({
  suppliers,
  onClose,
  onRemove,
}: {
  suppliers: SupplierData[];
  onClose: () => void;
  onRemove: (id: number) => void;
}) {
  const best = Math.max(...suppliers.map((s) => s.ranking.score));
  const cell = (s: SupplierData, field: string) => {
    const values = s.fields[field]?.map((v) => v.value) ?? [];
    return values.length ? values : null;
  };
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">Сравнение ({suppliers.length})</h2>
        <button onClick={onClose} className="text-sm text-slate-500 hover:text-slate-800">
          Закрыть
        </button>
      </div>
      <p className="mb-3 text-sm text-slate-500">
        Выбранные поставщики рядом, строка за строкой. Лучшая оценка выделена зелёным, «нет данных» значит, что агент не нашёл это на сайте. Телефоны, почты и
        сайты кликабельны.
      </p>
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
                  <button onClick={() => onRemove(s.id)} className="mt-0.5 block text-xs font-normal text-slate-400 hover:text-slate-700">
                    убрать из сравнения
                  </button>
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
            {suppliers.some((s) => s.ranking.warnings.length > 0) && (
              <tr className="border-t border-slate-100">
                <th className="sticky left-0 bg-white p-2 text-xs font-normal text-slate-400">Внимание</th>
                {suppliers.map((s) => (
                  <td key={s.id} className="p-2 text-amber-800">
                    {s.ranking.warnings.length ? s.ranking.warnings.join(" ") : <span className="italic text-slate-300">нет</span>}
                  </td>
                ))}
              </tr>
            )}
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
                      {value ? (
                        <div className="space-y-0.5">
                          {value.map((v) => (
                            <div key={v}>
                              <FieldText field={field} value={v} />
                            </div>
                          ))}
                        </div>
                      ) : (
                        <span className="italic text-slate-300">нет данных</span>
                      )}
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
