import type { Price, SupplierData } from "../api";
import { STATUS_ORDER, statusOf } from "./status";

export type SortKey = "rank" | "supplier" | "type" | "score" | "region" | "price" | "moq" | "delivery" | "phone" | "email" | "warnings" | "status";
export type SortState = { key: SortKey; dir: "asc" | "desc" };

const UNIT_RANK = { RUB: 0, kg: 1, l: 1, pcs: 2 } as const;
const BASE_LABEL = { kg: "кг", l: "л", pcs: "шт." } as const;

export const warnCount = (s: SupplierData): number => s.ranking.warnings.filter((w) => w.severity === "warn").length;
export const supplierType = (s: SupplierData): string => s.fields.supplier_type?.[0]?.value ?? "";

/** Compact headline price for the table: per base unit when known ("≈84 ₽/кг"), otherwise the amount. */
export function compactPrice(price: Price | null | undefined): string {
  if (!price || price.price_rub === null) return "—";
  const fmt = (n: number) => (n >= 10 ? Math.round(n) : Math.round(n * 10) / 10).toLocaleString("ru-RU");
  if (price.price_per_base_unit !== null && price.base_unit) return `≈${fmt(price.price_per_base_unit)} ₽/${BASE_LABEL[price.base_unit]}`;
  return `${fmt(price.price_rub)} ₽`;
}

/** Value used to sort a column; `null` always sorts last whatever the direction. */
export function sortValue(s: SupplierData, key: SortKey): string | number | null {
  switch (key) {
    case "rank":
      return s.ranking.rank;
    case "score":
      return s.ranking.score;
    case "supplier":
      return s.name.toLowerCase();
    case "type":
      return supplierType(s) || null;
    case "region":
      return s.ranking.tier_label;
    case "price":
      return s.commerce?.price_headline?.price_per_base_unit ?? s.commerce?.price_headline?.price_rub ?? null;
    case "moq": {
      const order = s.commerce?.min_order;
      return order && order.value !== null && order.unit ? UNIT_RANK[order.unit] * 1e9 + order.value : null;
    }
    case "delivery":
      return s.fields.delivery_terms?.[0]?.value.toLowerCase() ?? null;
    case "phone":
      return s.fields.phone?.[0]?.value ?? null;
    case "email":
      return s.fields.email?.[0]?.value.toLowerCase() ?? null;
    case "warnings":
      return warnCount(s);
    case "status":
      return STATUS_ORDER.indexOf(statusOf(s));
  }
}

export function sortSuppliers(list: SupplierData[], { key, dir }: SortState): SupplierData[] {
  const sign = dir === "asc" ? 1 : -1;
  return [...list].sort((a, b) => {
    const va = sortValue(a, key);
    const vb = sortValue(b, key);
    if (va === null && vb === null) return a.ranking.rank - b.ranking.rank;
    if (va === null) return 1;
    if (vb === null) return -1;
    const cmp = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb), "ru");
    return cmp !== 0 ? sign * cmp : a.ranking.rank - b.ranking.rank;
  });
}
