import { useState } from "react";
import type { CatalogStatus } from "../api";
import CatalogPanel from "./CatalogPanel";
import CategoriesTree from "./CategoriesTree";
import ItemsList from "./ItemsList";

/** Вкладка «Справочник»: категории для поиска и позиции меню, из которых они выведены. */
export default function CatalogPage() {
  const [catalog, setCatalog] = useState<CatalogStatus | null>(null);
  const ready = catalog !== null && catalog.active_items > 0;
  return (
    <div className="space-y-5">
      <CatalogPanel onStatus={setCatalog} />
      {ready && <CategoriesTree />}
      {ready && <ItemsList />}
    </div>
  );
}
