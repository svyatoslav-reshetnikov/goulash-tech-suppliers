import type { SupplierStatus } from "../api";
import { STATUS_LABELS, STATUS_ORDER } from "../lib/status";

const TONE: Record<SupplierStatus, string> = {
  new: "border-slate-300 bg-white text-slate-700",
  called: "border-cyan-300 bg-cyan-50 text-cyan-900",
  awaiting_price: "border-amber-300 bg-amber-50 text-amber-900",
  shortlisted: "border-emerald-300 bg-emerald-50 text-emerald-900",
  declined: "border-slate-300 bg-slate-100 text-slate-500",
};

/** Work status of a supplier within the search. */
export default function StatusSelect({ value, onChange, label }: { value: SupplierStatus; onChange: (status: SupplierStatus) => void; label: string }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as SupplierStatus)}
      aria-label={label}
      className={`rounded-lg border px-2 py-1 text-xs ${TONE[value]}`}
    >
      {STATUS_ORDER.map((s) => (
        <option key={s} value={s}>
          {STATUS_LABELS[s]}
        </option>
      ))}
    </select>
  );
}
