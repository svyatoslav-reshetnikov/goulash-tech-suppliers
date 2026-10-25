import { useState } from "react";
import { saveSupplierNote, type FieldValue, type SupplierData } from "../api";
import { MinOrderLine, PriceBlock } from "./CommerceBlock";
import { FieldText } from "./Linkified";
import RankBadge from "./RankBadge";

const LABELS: Record<string, string> = {
  phone: "Телефон",
  email: "Email",
  address: "Адрес",
  description: "Чем занимается",
  assortment: "Ассортимент",
  regions: "Регионы поставки",
  delivery_terms: "Доставка",
  moq: "Минимальный заказ",
  price_hint: "Цены",
  certificates: "Сертификаты",
};
const MISSING_LABELS: Record<string, string> = {
  phone: "телефон",
  description: "чем занимается",
  address: "адрес",
  regions: "регионы работы",
  delivery_terms: "условия доставки",
  moq: "минимальный заказ",
  price_hint: "цены",
  certificates: "сертификаты",
};
const ORDER = ["phone", "email", "address", "description", "assortment", "regions", "delivery_terms", "moq", "price_hint", "certificates"];

function Value({ field, item }: { field: string; item: FieldValue }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="text-sm">
      <FieldText field={field} value={item.value} />
      <button onClick={() => setOpen(!open)} className="ml-1.5 text-xs text-slate-500 underline decoration-dotted" title="Показать, откуда взято">
        {open ? "скрыть источник" : "источник"}
      </button>
      {open && (
        <blockquote className="mt-1 rounded border-l-2 border-cyan-300 bg-slate-50 px-3 py-1.5 text-xs text-slate-600">
          «{item.quote}»
          <a href={item.source_url} target="_blank" rel="noreferrer" className="mt-1 block truncate text-cyan-700 hover:underline">
            {item.source_url}
          </a>
          <span className="text-slate-400">проверено {new Date(item.verified_at).toLocaleDateString("ru-RU")}</span>
        </blockquote>
      )}
    </div>
  );
}

/** Карточка поставщика: у каждого поля можно открыть дословную цитату и страницу, откуда оно взято. */
type CardProps = {
  supplier: SupplierData;
  selected?: boolean;
  canSelect?: boolean;
  onToggle?: () => void;
  onNote?: (supplierId: number, note: string | null) => void;
};

const PARTS: [keyof SupplierData["ranking"]["breakdown"], string][] = [
  ["relevance", "Совпадение с продуктом"],
  ["location", "Расположение"],
  ["completeness", "Полнота данных"],
  ["reliability", "Надёжность"],
];

/** Заметка закупщика: хранится у поставщика и видна в любом запуске, где он найден. */
function NoteEditor({ supplier, onNote }: { supplier: SupplierData; onNote?: CardProps["onNote"] }) {
  const [text, setText] = useState(supplier.note ?? "");
  const [state, setState] = useState<"saved" | "saving" | "error">("saved");
  const dirty = text.trim() !== (supplier.note ?? "");

  const save = async () => {
    setState("saving");
    try {
      const stored = await saveSupplierNote(supplier.id, text);
      setText(stored ?? "");
      onNote?.(supplier.id, stored);
      setState("saved");
    } catch {
      setState("error");
    }
  };

  return (
    <div className="mt-3 border-t border-slate-100 pt-3">
      <label className="mb-1 block text-xs text-slate-400">Заметка</label>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        maxLength={2000}
        rows={2}
        placeholder="Например: звонили 25.10, обещали прайс до пятницы"
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-cyan-500"
      />
      <div className="mt-1 flex items-center gap-3">
        <button
          onClick={save}
          disabled={!dirty || state === "saving"}
          className="rounded-lg bg-slate-900 px-3 py-1 text-xs text-white hover:bg-slate-700 disabled:opacity-40"
        >
          {state === "saving" ? "Сохраняем…" : "Сохранить заметку"}
        </button>
        {state === "error" && <span className="text-xs text-red-600">Не удалось сохранить</span>}
        {state === "saved" && !dirty && supplier.note && <span className="text-xs text-slate-400">Сохранено</span>}
      </div>
    </div>
  );
}

export default function SupplierCard({ supplier, selected = false, canSelect = true, onToggle, onNote }: CardProps) {
  const [why, setWhy] = useState(false);
  const ranking = supplier.ranking;
  const type = supplier.fields.supplier_type?.[0];
  const names = supplier.fields.name ?? [];
  const rows = ORDER.filter((f) => supplier.fields[f]?.length);
  return (
    <article className={`rounded-xl border p-4 shadow-sm ${ranking.recommended ? "border-emerald-300 bg-emerald-50/40" : "border-slate-200 bg-white"}`}>
      <header className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-base font-semibold">{supplier.name}</h3>
        {type && <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-800">{type.value}</span>}
      </header>
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <span className="flex flex-wrap items-center gap-2">
          <RankBadge ranking={ranking} />
          {ranking.recommended && (
            <span className="rounded-full bg-emerald-600 px-2.5 py-0.5 text-xs font-medium text-white">Рекомендуем связаться</span>
          )}
        </span>
        {onToggle && (
          <label className={`flex items-center gap-1.5 text-xs ${selected || canSelect ? "text-slate-600" : "text-slate-300"}`}>
            <input type="checkbox" checked={selected} disabled={!selected && !canSelect} onChange={onToggle} />
            Сравнить
          </label>
        )}
      </div>
      <div className="my-2 flex flex-wrap gap-1.5">
        {ranking.warnings.map((w) => (
          <span key={w.code} className={`rounded-full px-2.5 py-0.5 text-xs ${w.severity === "warn" ? "bg-amber-100 text-amber-900" : "bg-slate-100 text-slate-600"}`}>
            {w.severity === "warn" ? "⚠ " : ""}
            {w.text}
          </span>
        ))}
      </div>
      <a href={supplier.website} target="_blank" rel="noreferrer" className="text-sm text-cyan-700 hover:underline">
        {supplier.domain}
      </a>
      <button onClick={() => setWhy(!why)} className="ml-3 text-xs text-slate-500 underline decoration-dotted">
        {why ? "скрыть оценку" : "почему такая оценка"}
      </button>
      {why && (
        <div className="mt-2 rounded-lg bg-slate-50 p-3 text-sm">
          <div className="space-y-1">
            {PARTS.map(([key, label]) => {
              const max = ranking.breakdown_max[key];
              return (
                <div key={key} className="grid grid-cols-[10rem_1fr_3rem] items-center gap-2 text-xs">
                  <span className="text-slate-500">{label}</span>
                  <span className="h-1.5 overflow-hidden rounded bg-slate-200">
                    <span className="block h-full bg-cyan-500" style={{ width: `${(ranking.breakdown[key] / max) * 100}%` }} />
                  </span>
                  <span className="text-right text-slate-500">{ranking.breakdown[key]}/{max}</span>
                </div>
              );
            })}
            {ranking.evidence && <p className="pt-1 text-xs text-slate-500">Товар: {ranking.evidence.label}</p>}
          </div>
          {!ranking.recommended && (
            <>
              <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-slate-600">
                {ranking.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
              {ranking.questions.length > 0 && (
                <div className="mt-2 text-xs text-slate-600">
                  <b>Что уточнить:</b>
                  <ul className="mt-1 list-disc space-y-0.5 pl-5">
                    {ranking.questions.map((q) => (
                      <li key={q}>{q}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
          {ranking.missing.length > 0 && <p className="mt-1 text-xs text-slate-400">Нет данных: {ranking.missing.join(", ")}</p>}
        </div>
      )}
      {supplier.research && (
        <p className={`mt-2 text-xs ${supplier.research.missing.length ? "text-amber-700" : "text-emerald-700"}`}>
          Изучено страниц сайта: {supplier.research.pages.length}.{" "}
          {supplier.research.missing.length
            ? `Не найдено на сайте: ${supplier.research.missing.map((f) => MISSING_LABELS[f] ?? f).join(", ")}.`
            : "Все обязательные данные найдены."}
        </p>
      )}
      {supplier.research?.qualification?.reason && (
        <p className="mt-1 text-xs text-slate-500">Проверка сайта перед изучением: {supplier.research.qualification.reason}</p>
      )}
      {ranking.recommended && (
        <div className="mt-3 space-y-2 text-sm">
          <ul className="list-disc space-y-0.5 pl-5 text-slate-600">
            {ranking.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          {ranking.questions.length > 0 && (
            <div className="rounded bg-white p-2.5 text-slate-700 ring-1 ring-emerald-100">
              <b>Что уточнить при звонке:</b>
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                {ranking.questions.map((q) => (
                  <li key={q}>{q}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      {names.length > 1 && <p className="mt-1 text-xs text-slate-400">Также: {names.slice(1).map((n) => n.value).join("; ")}</p>}
      <dl className="mt-3 space-y-1.5">
        {rows.map((field) => {
          // structured price and minimum order replace the raw strings; old responses without them show the raw values
          const priceBlock = field === "price_hint" && supplier.commerce;
          const minOrder = field === "moq" ? supplier.commerce?.min_order : null;
          return (
            <div key={field} className="grid grid-cols-[8.5rem_1fr] gap-2">
              <dt className="text-xs text-slate-400">{priceBlock ? "Цена на искомый товар" : LABELS[field]}</dt>
              <dd className="space-y-1">
                {priceBlock && supplier.commerce ? (
                  <PriceBlock commerce={supplier.commerce} />
                ) : minOrder ? (
                  <div className="text-sm">
                    <MinOrderLine order={minOrder} />
                  </div>
                ) : (
                  supplier.fields[field].map((item) => <Value key={`${field}-${item.value}`} field={field} item={item} />)
                )}
              </dd>
            </div>
          );
        })}
        {supplier.inn && (
          <div className="grid grid-cols-[8.5rem_1fr] gap-2">
            <dt className="text-xs text-slate-400">ИНН</dt>
            <dd className="text-sm">
              {supplier.inn}{" "}
              <a href={`https://checko.ru/search?query=${supplier.inn}`} target="_blank" rel="noreferrer" className="text-xs text-cyan-700 hover:underline">
                проверить в реестре
              </a>
            </dd>
          </div>
        )}
        {rows.length === 0 && !supplier.inn && <p className="text-sm text-slate-400">Других данных агент не нашёл.</p>}
      </dl>
      <NoteEditor supplier={supplier} onNote={onNote} />
    </article>
  );
}
