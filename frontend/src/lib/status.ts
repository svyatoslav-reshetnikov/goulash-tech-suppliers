import type { SupplierData, SupplierStatus } from "../api";

export const STATUS_LABELS: Record<SupplierStatus, string> = {
  new: "Новый",
  called: "Позвонили",
  awaiting_price: "Ждём прайс",
  shortlisted: "В шорт-листе",
  declined: "Отказались",
};
export const STATUS_ORDER = Object.keys(STATUS_LABELS) as SupplierStatus[];
export const statusOf = (s: SupplierData): SupplierStatus => s.status?.value ?? "new";

/** "24.10.2026" for an ISO timestamp; empty when absent or invalid. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("ru-RU");
}
