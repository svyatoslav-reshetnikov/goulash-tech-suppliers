import type { SupplierData } from "../api";
import { compactPrice, sortSuppliers, supplierType, warnCount, type SortKey, type SortState } from "../lib/results";
import { FieldText } from "./Linkified";

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "rank", label: "#" },
  { key: "supplier", label: "Поставщик" },
  { key: "type", label: "Тип" },
  { key: "score", label: "Оценка" },
  { key: "region", label: "Регион" },
  { key: "price", label: "Цена на товар" },
  { key: "moq", label: "Мин. заказ" },
  { key: "delivery", label: "Доставка" },
  { key: "phone", label: "Телефон" },
  { key: "email", label: "Email" },
  { key: "warnings", label: "⚠" },
];

type Props = {
  suppliers: SupplierData[];
  selected: number[];
  onToggleSelect: (id: number) => void;
  canSelectMore: boolean;
  sort: SortState;
  onSort: (sort: SortState) => void;
};

/** Sortable table of the same suppliers as the cards; scrolls horizontally on narrow screens. */
export default function SupplierTable({ suppliers, selected, onToggleSelect, canSelectMore, sort, onSort }: Props) {
  const rows = sortSuppliers(suppliers, sort);
  const click = (key: SortKey) => onSort(sort.key === key ? { key, dir: sort.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "score" ? "desc" : "asc" });

  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
      <table className="w-full min-w-[56rem] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs text-slate-600">
            <th className="p-2">
              <span className="sr-only">Сравнить</span>
            </th>
            {COLUMNS.map((c) => (
              <th key={c.key} className="p-0" aria-sort={sort.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                <button onClick={() => click(c.key)} className="w-full whitespace-nowrap px-2 py-2 text-left font-medium hover:bg-slate-50">
                  {c.label}
                  {sort.key === c.key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="align-top">
          {rows.map((s) => {
            const isSelected = selected.includes(s.id);
            const phone = s.fields.phone?.[0]?.value;
            const email = s.fields.email?.[0]?.value;
            const price = s.commerce?.price_headline;
            return (
              <tr key={s.id} className={`border-b border-slate-100 ${s.ranking.recommended ? "bg-emerald-50/40" : ""}`}>
                <td className="p-2">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    disabled={!isSelected && !canSelectMore}
                    onChange={() => onToggleSelect(s.id)}
                    aria-label={`Сравнить: ${s.name}`}
                  />
                </td>
                <td className="p-2 text-slate-600">{s.ranking.rank}</td>
                <td className="p-2 font-medium">
                  {s.name}
                  {s.ranking.recommended && <span className="ml-1.5 rounded-full bg-emerald-600 px-1.5 py-0.5 text-[10px] font-medium text-white">рекомендуем</span>}
                </td>
                <td className="p-2 text-slate-600">{supplierType(s) || "—"}</td>
                <td className="p-2 font-semibold">{s.ranking.score}</td>
                <td className="p-2 text-slate-700">{s.ranking.tier_label}</td>
                <td className="p-2" title={price?.display}>
                  {compactPrice(price)}
                  {price?.needs_confirmation && <span className="ml-1 text-xs text-amber-700">уточнить</span>}
                </td>
                <td className="p-2">{s.commerce?.min_order?.display ?? "—"}</td>
                <td className="max-w-[14rem] p-2 text-slate-700">{s.fields.delivery_terms?.[0]?.value ?? "—"}</td>
                <td className="whitespace-nowrap p-2">{phone ? <FieldText field="phone" value={phone} /> : "—"}</td>
                <td className="p-2">{email ? <FieldText field="email" value={email} /> : "—"}</td>
                <td className="p-2 text-amber-800" title={s.ranking.warnings.filter((w) => w.severity === "warn").map((w) => w.text).join("\n")}>
                  {warnCount(s) || ""}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
