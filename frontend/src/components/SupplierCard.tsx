import { useState } from "react";
import { saveSupplierNote, type FieldValue, type SupplierData } from "../api";
import { safeUrl } from "../lib/safeUrl";
import { MinOrderLine, PriceBlock, PriceLine } from "./CommerceBlock";
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
const PARTS: [keyof SupplierData["ranking"]["breakdown"], string][] = [
  ["relevance", "Совпадение с продуктом"],
  ["location", "Расположение"],
  ["completeness", "Полнота данных"],
  ["reliability", "Надёжность"],
];

/** Link to the page a value was taken from; the quote is in the tooltip. Shown only when the page-level toggle is on. */
export function SourceLink({ url, quote }: { url: string; quote?: string }) {
  const href = safeUrl(url);
  if (!href) return null;
  return (
    <a href={href} target="_blank" rel="noreferrer" title={quote ? `«${quote}»` : undefined} className="ml-1.5 text-xs text-slate-500 underline decoration-dotted hover:text-cyan-700">
      источник
    </a>
  );
}

function Value({ field, item, showSources }: { field: string; item: FieldValue; showSources: boolean }) {
  return (
    <div className="text-sm">
      <FieldText field={field} value={item.value} />
      {showSources && <SourceLink url={item.source_url} quote={item.quote} />}
    </div>
  );
}

/** Заметка закупщика: хранится у поставщика и видна в любом запуске, где он найден. */
function NoteEditor({ supplier, onNote }: { supplier: SupplierData; onNote?: (supplierId: number, note: string | null) => void }) {
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
      <label htmlFor={`note-${supplier.id}`} className="mb-1 block text-xs text-slate-500">
        Заметка
      </label>
      <textarea
        id={`note-${supplier.id}`}
        value={text}
        onChange={(e) => setText(e.target.value)}
        maxLength={2000}
        rows={2}
        placeholder="Например: звонили 25.10, обещали прайс до пятницы"
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-cyan-500"
      />
      <div className="mt-1 flex items-center gap-3">
        <button onClick={save} disabled={!dirty || state === "saving"} className="rounded-lg bg-slate-900 px-3 py-1 text-xs text-white hover:bg-slate-700 disabled:opacity-40">
          {state === "saving" ? "Сохраняем…" : "Сохранить заметку"}
        </button>
        {state === "error" && <span className="text-xs text-red-600">Не удалось сохранить</span>}
        {state === "saved" && !dirty && supplier.note && <span className="text-xs text-slate-500">Сохранено</span>}
      </div>
    </div>
  );
}

export type CardProps = {
  supplier: SupplierData;
  expanded: boolean;
  onToggleExpanded: () => void;
  showSources: boolean;
  selected?: boolean;
  canSelect?: boolean;
  onToggleSelect?: () => void;
  onNote?: (supplierId: number, note: string | null) => void;
};

/** "Уточните цены на «Рис»." -> "цены на «Рис»": the collapsed card already says "Уточнить:". */
export function asClause(question: string): string {
  const rest = question.replace(/^(Уточните|Узнайте|Попросите)\s+/, "").replace(/\.$/, "");
  return rest.charAt(0).toLowerCase() + rest.slice(1);
}

/** Main phone and email of a supplier for the collapsed card. */
export function mainContacts(supplier: SupplierData): { phone?: string; email?: string } {
  return { phone: supplier.fields.phone?.[0]?.value, email: supplier.fields.email?.[0]?.value };
}

/** Collapsed: one compact card (name, score, headline price, contacts, warnings). Expanded: everything the agent collected. */
export default function SupplierCard({ supplier, expanded, onToggleExpanded, showSources, selected = false, canSelect = true, onToggleSelect, onNote }: CardProps) {
  const ranking = supplier.ranking;
  const type = supplier.fields.supplier_type?.[0];
  const names = supplier.fields.name ?? [];
  const rows = ORDER.filter((f) => supplier.fields[f]?.length);
  const { phone, email } = mainContacts(supplier);
  const commerce = supplier.commerce;
  const warns = ranking.warnings.filter((w) => w.severity === "warn");
  const website = safeUrl(supplier.website);
  const price = commerce?.price_headline;
  const detailsId = `supplier-details-${supplier.id}`;

  return (
    <article
      aria-label={supplier.name}
      className={`rounded-xl border p-4 shadow-sm ${ranking.recommended ? "border-emerald-300 bg-emerald-50/40" : "border-slate-200 bg-white"}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <RankBadge ranking={ranking} />
            <h3 className="text-base font-semibold">
              {website ? (
                <a href={website} target="_blank" rel="noreferrer" className="hover:underline">
                  {supplier.name}
                </a>
              ) : (
                supplier.name
              )}
            </h3>
            {type && <span className="text-sm text-slate-500">{type.value}</span>}
            {ranking.recommended && <span className="rounded-full bg-emerald-600 px-2.5 py-0.5 text-xs font-medium text-white">Рекомендуем связаться</span>}
          </div>
          <p className="text-sm text-slate-700">
            {price ? (
              <PriceLine price={price} />
            ) : (
              <span className="text-slate-500">{supplier.fields.price_hint?.length ? "Цена на искомый товар не найдена" : "Цена не найдена"}</span>
            )}
            <span className="text-slate-400"> · </span>
            {commerce?.min_order ? (
              <>
                мин. заказ: <MinOrderLine order={commerce.min_order} />
              </>
            ) : (
              <span className="text-slate-500">мин. заказ не найден</span>
            )}
          </p>
          <p className="text-sm text-slate-700">
            {phone || email ? (
              <>
                {phone && <FieldText field="phone" value={phone} />}
                {phone && email && <span className="text-slate-400"> · </span>}
                {email && <FieldText field="email" value={email} />}
              </>
            ) : (
              <span className="text-slate-500">Контакты не найдены</span>
            )}
          </p>
          {warns.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-0.5">
              {warns.map((w) => (
                <span key={w.code} className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs text-amber-900">
                  ⚠ {w.text}
                </span>
              ))}
            </div>
          )}
          {ranking.questions[0] && <p className="text-xs text-slate-600">Уточнить: {asClause(ranking.questions[0])}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {onToggleSelect && (
            <label className={`flex items-center gap-1.5 text-xs ${selected || canSelect ? "text-slate-700" : "text-slate-400"}`}>
              <input type="checkbox" checked={selected} disabled={!selected && !canSelect} onChange={onToggleSelect} aria-label={`Сравнить: ${supplier.name}`} />
              Сравнить
            </label>
          )}
          <button
            onClick={onToggleExpanded}
            aria-expanded={expanded}
            aria-controls={detailsId}
            className="rounded-lg border border-slate-300 px-3 py-1 text-sm text-slate-700 hover:bg-slate-50"
          >
            {expanded ? "Свернуть ▴" : "Подробнее ▾"}
          </button>
        </div>
      </div>

      {expanded && (
        <div id={detailsId} className="mt-3 border-t border-slate-100 pt-3">
          <div className="mb-3 rounded-lg bg-slate-50 p-3 text-sm">
            <div className="space-y-1">
              {PARTS.map(([key, label]) => {
                const max = ranking.breakdown_max[key];
                return (
                  <div key={key} className="grid grid-cols-[10rem_1fr_3rem] items-center gap-2 text-xs">
                    <span className="text-slate-600">{label}</span>
                    <span className="h-1.5 overflow-hidden rounded bg-slate-200">
                      <span className="block h-full bg-cyan-600" style={{ width: `${(ranking.breakdown[key] / max) * 100}%` }} />
                    </span>
                    <span className="text-right text-slate-600">
                      {ranking.breakdown[key]}/{max}
                    </span>
                  </div>
                );
              })}
              {ranking.evidence && <p className="pt-1 text-xs text-slate-600">Товар: {ranking.evidence.label}</p>}
            </div>
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-slate-600">
              {ranking.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            {ranking.questions.length > 0 && (
              <div className="mt-2 text-xs text-slate-700">
                <b>Что уточнить при звонке:</b>
                <ul className="mt-1 list-disc space-y-0.5 pl-5">
                  {ranking.questions.map((q) => (
                    <li key={q}>{q}</li>
                  ))}
                </ul>
              </div>
            )}
            {ranking.warnings.some((w) => w.severity === "info") && (
              <p className="mt-2 text-xs text-slate-500">{ranking.warnings.filter((w) => w.severity === "info").map((w) => w.text).join(" · ")}</p>
            )}
          </div>
          {supplier.research && (
            <p className={`mb-2 text-xs ${supplier.research.missing.length ? "text-amber-800" : "text-emerald-800"}`}>
              Изучено страниц сайта: {supplier.research.pages.length}.{" "}
              {supplier.research.missing.length
                ? `Не найдено на сайте: ${supplier.research.missing.map((f) => MISSING_LABELS[f] ?? f).join(", ")}.`
                : "Все обязательные данные найдены."}
            </p>
          )}
          {supplier.research?.qualification?.reason && (
            <p className="mb-2 text-xs text-slate-600">Проверка сайта перед изучением: {supplier.research.qualification.reason}</p>
          )}
          {names.length > 1 && <p className="mb-2 text-xs text-slate-500">Также: {names.slice(1).map((n) => n.value).join("; ")}</p>}
          <dl className="space-y-1.5">
            {rows.map((field) => {
              // structured price and minimum order replace the raw strings; old responses without them show the raw values
              const priceBlock = field === "price_hint" && commerce;
              const minOrder = field === "moq" ? commerce?.min_order : null;
              return (
                <div key={field} className="grid grid-cols-[8.5rem_1fr] gap-2">
                  <dt className="text-xs text-slate-500">{priceBlock ? "Цена на искомый товар" : LABELS[field]}</dt>
                  <dd className="space-y-1">
                    {priceBlock && commerce ? (
                      <>
                        <PriceBlock commerce={commerce} />
                        {showSources && commerce.price_headline && <SourceLink url={commerce.price_headline.source_ref} quote={commerce.price_headline.raw} />}
                      </>
                    ) : minOrder ? (
                      <div className="text-sm">
                        <MinOrderLine order={minOrder} />
                        {showSources && <SourceLink url={minOrder.source_ref} quote={minOrder.raw} />}
                      </div>
                    ) : (
                      supplier.fields[field].map((item) => <Value key={`${field}-${item.value}`} field={field} item={item} showSources={showSources} />)
                    )}
                  </dd>
                </div>
              );
            })}
            {supplier.inn && (
              <div className="grid grid-cols-[8.5rem_1fr] gap-2">
                <dt className="text-xs text-slate-500">ИНН</dt>
                <dd className="text-sm">
                  {supplier.inn}{" "}
                  <a href={`https://checko.ru/search?query=${encodeURIComponent(supplier.inn)}`} target="_blank" rel="noreferrer" className="text-xs text-cyan-700 hover:underline">
                    проверить в реестре
                  </a>
                </dd>
              </div>
            )}
            {rows.length === 0 && !supplier.inn && <p className="text-sm text-slate-500">Других данных агент не нашёл.</p>}
          </dl>
          <NoteEditor supplier={supplier} onNote={onNote} />
        </div>
      )}
    </article>
  );
}
