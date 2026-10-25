import { useState } from "react";
import type { ExtrasInput } from "../api";

export const DOCUMENT_LABELS = { haccp: "ХАССП", iso: "ISO", mercury: "Ветеринарные документы (Меркурий)" } as const;
export const isEmptyExtras = (e: ExtrasInput): boolean =>
  !e.volume_value && !(e.documents && e.documents.length) && !e.storage && !(e.delivery && e.delivery.trim());

/** Optional block «Дополнительно» (collapsed by default): volume, documents, storage, delivery. Passed to the agent; no impact on the score. */
export default function ExtrasPanel({ value, onChange }: { value: ExtrasInput; onChange: (value: ExtrasInput) => void }) {
  const [open, setOpen] = useState(false);
  const documents = value.documents ?? [];
  const filled = !isEmptyExtras(value);

  return (
    <div className="rounded-lg border border-slate-200">
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls="extras-body"
        className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium text-slate-700"
      >
        <span>
          Дополнительно
          {filled && <span className="ml-2 rounded-full bg-cyan-100 px-2 py-0.5 text-xs font-normal text-cyan-900">заполнено</span>}
        </span>
        <span aria-hidden="true">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div id="extras-body" className="space-y-4 border-t border-slate-100 px-3 py-3 text-sm">
          <p className="text-xs text-slate-600">
            Необязательно. Эти требования передаются агенту; если подтверждения на сайте нет, это будет отмечено предупреждением и вопросом для звонка. На оценку они не влияют.
          </p>
          <div>
            <label htmlFor="extras-volume" className="mb-1 block text-slate-700">
              Объём закупки в месяц
            </label>
            <div className="flex gap-2">
              <input
                id="extras-volume"
                type="number"
                min={0}
                value={value.volume_value ?? ""}
                onChange={(e) => onChange({ ...value, volume_value: e.target.value === "" ? null : Number(e.target.value) })}
                className="w-32 rounded-lg border border-slate-300 px-3 py-1.5 outline-none focus:border-cyan-500"
              />
              <select
                aria-label="Единица объёма"
                value={value.volume_unit ?? "кг"}
                onChange={(e) => onChange({ ...value, volume_unit: e.target.value as ExtrasInput["volume_unit"] })}
                className="rounded-lg border border-slate-300 bg-white px-2 py-1.5"
              >
                {["кг", "т", "л", "шт"].map((u) => (
                  <option key={u}>{u}</option>
                ))}
              </select>
            </div>
          </div>
          <fieldset>
            <legend className="mb-1 text-slate-700">Нужные документы</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {(Object.keys(DOCUMENT_LABELS) as (keyof typeof DOCUMENT_LABELS)[]).map((key) => (
                <label key={key} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={documents.includes(key)}
                    onChange={(e) => onChange({ ...value, documents: e.target.checked ? [...documents, key] : documents.filter((d) => d !== key) })}
                  />
                  {DOCUMENT_LABELS[key]}
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend className="mb-1 text-slate-700">Хранение</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {([[null, "Не важно"], ["chilled", "Охлаждённое"], ["frozen", "Замороженное"]] as const).map(([key, label]) => (
                <label key={label} className="flex items-center gap-1.5">
                  <input type="radio" name="extras-storage" checked={(value.storage ?? null) === key} onChange={() => onChange({ ...value, storage: key })} />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          <div>
            <label htmlFor="extras-delivery" className="mb-1 block text-slate-700">
              Условия доставки
            </label>
            <input
              id="extras-delivery"
              maxLength={200}
              value={value.delivery ?? ""}
              onChange={(e) => onChange({ ...value, delivery: e.target.value })}
              placeholder="Например: до 8 утра, по будням, на склад в Мытищах"
              className="w-full rounded-lg border border-slate-300 px-3 py-1.5 outline-none focus:border-cyan-500"
            />
          </div>
        </div>
      )}
    </div>
  );
}
