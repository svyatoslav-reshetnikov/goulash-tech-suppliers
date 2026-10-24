import type { SupplierData } from "../api";
import RankBadge from "./RankBadge";

/** «С кем связаться»: лучшие поставщики с объяснением, чего не хватает и что спросить при звонке. */
export default function RecommendationPanel({ suppliers }: { suppliers: SupplierData[] }) {
  const top = suppliers.filter((s) => s.ranking.recommended);
  if (suppliers.length === 0) return null;
  if (top.length === 0) {
    return (
      <section className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
        Рекомендовать пока некого: по найденным поставщикам слишком мало подтверждённых данных (оценка ниже 40 из 100).
      </section>
    );
  }
  return (
    <section className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-5 shadow-sm">
      <h2 className="mb-1 text-lg font-semibold">Рекомендуем связаться</h2>
      <p className="mb-4 text-sm text-slate-500">
        Оценка считается по данным со ссылками на источники: совпадение с продуктом, близость к вашему региону, полнота и надёжность.
      </p>
      <ol className="space-y-4">
        {top.map((s) => {
          const phones = s.fields.phone ?? [];
          const emails = s.fields.email ?? [];
          return (
            <li key={s.id} className="rounded-lg border border-emerald-100 bg-white p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="font-semibold">{s.name}</h3>
                <RankBadge ranking={s.ranking} />
              </div>
              <a href={s.website} target="_blank" rel="noreferrer" className="text-sm text-cyan-700 hover:underline">
                {s.domain}
              </a>
              <p className="mt-1 text-sm text-slate-700">
                {[...phones.map((p) => p.value), ...emails.map((e) => e.value)].join(" · ") || "Контакты не найдены"}
              </p>
              <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm text-slate-600">
                {s.ranking.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
              {s.ranking.questions.length > 0 && (
                <div className="mt-2 rounded bg-slate-50 p-2.5 text-sm text-slate-700">
                  <b>Что уточнить:</b>
                  <ul className="mt-1 list-disc space-y-0.5 pl-5">
                    {s.ranking.questions.map((q) => (
                      <li key={q}>{q}</li>
                    ))}
                  </ul>
                </div>
              )}
              {s.ranking.missing.length > 0 && (
                <p className="mt-2 text-xs text-slate-400">Нет данных: {s.ranking.missing.join(", ")}</p>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
