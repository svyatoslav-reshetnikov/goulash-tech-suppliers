/** Сессия закончилась или входа не было: любой ответ 401 сообщает об этом приложению, и оно показывает страницу входа. */
export const UNAUTHORIZED_EVENT = "goulash:unauthorized";

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(input, init);
  if (response.status === 401 && !input.startsWith("/api/auth/")) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  return response;
}

export type AuthState = { auth_required: boolean; authenticated: boolean; user: string | null };

export async function fetchAuth(): Promise<AuthState> {
  const response = await apiFetch("/api/auth/me");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as AuthState;
}

/** Вход по логину и паролю. Возвращает состояние входа или текст ошибки для показа под формой. */
export async function login(username: string, password: string): Promise<AuthState | { error: string }> {
  const response = await apiFetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (response.ok) return (await response.json()) as AuthState;
  const detail = ((await response.json().catch(() => ({}))) as { detail?: string }).detail;
  return { error: detail ?? `Не удалось войти (HTTP ${response.status})` };
}

export async function logout(): Promise<void> {
  await apiFetch("/api/auth/logout", { method: "POST" });
}

export type Health = { status: string; database: string };

export type CatalogStatus = {
  state: "empty" | "loading" | "ready";
  active_items: number;
  categories_active: number;
};

export type CatalogItem = {
  source_id: number;
  name: string;
  description: string;
  weight: string | null;
  sections: string[];
};

export async function fetchHealth(): Promise<Health> {
  const response = await apiFetch("/api/health");
  // 503 тоже содержит тело с описанием состояния
  return (await response.json()) as Health;
}

export async function fetchCatalogStatus(): Promise<CatalogStatus> {
  const response = await apiFetch("/api/catalog/status");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as CatalogStatus;
}

export async function fetchCatalogItems(
  q: string,
  offset: number,
  limit = 30,
): Promise<{ total: number; items: CatalogItem[] }> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (q.trim()) params.set("q", q.trim());
  const response = await apiFetch(`/api/catalog/items?${params}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as { total: number; items: CatalogItem[] };
}

export type CategoryNode = { id: number; name: string; synonyms: string[]; origin: "derived" | "manual"; items: number };
export type CategoryGroup = { id: number; name: string; kind: "ingredient" | "packaging"; categories: CategoryNode[] };

export async function fetchCategories(): Promise<{ total: number; groups: CategoryGroup[] }> {
  const response = await apiFetch("/api/categories");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as { total: number; groups: CategoryGroup[] };
}

export type LocationMatch = {
  kind: "city" | "region";
  key: string;
  label: string;
  region_id: string;
  region_name: string;
  population: number | null;
};

export type SearchRegion = { id: string; name: string; role: "primary" | "neighbor" };
export type ResolvedLocation = { selected: LocationMatch; regions: SearchRegion[] };

export async function searchLocations(q: string): Promise<LocationMatch[]> {
  const response = await apiFetch(`/api/locations?${new URLSearchParams({ q, limit: "8" })}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return ((await response.json()) as { results: LocationMatch[] }).results;
}

export async function resolveLocation(selected: LocationMatch, neighbors: boolean): Promise<ResolvedLocation> {
  const params = new URLSearchParams({ kind: selected.kind, key: selected.key, neighbors: String(neighbors) });
  const response = await apiFetch(`/api/locations/resolve?${params}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as ResolvedLocation;
}

// --- запуски агента ---

export type RunStatus = "queued" | "running" | "done" | "failed";
export type RunStats = {
  turns?: number;
  tool_calls?: number;
  searches?: number;
  fetches?: number;
  suppliers_saved?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  seconds?: number;
  cost_rub?: number;
};
export type Run = {
  id: number;
  status: RunStatus;
  stop_reason: string | null;
  category: string;
  /** true for a product typed in by hand (not in the catalog) */
  free_text?: boolean;
  /** id of the first run of a multi-product search; null for a single search */
  group_id?: number | null;
  extras?: Extras | null;
  location: string | null;
  include_neighbors: boolean;
  regions: SearchRegion[];
  supplier_type: string | null;
  summary: string | null;
  error: string | null;
  stats: RunStats | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};
export type RunEvent = { seq?: number; type: string; message?: string; created_at?: string; [key: string]: unknown };
export type FieldValue = { value: string; quote: string; source_url: string; verified_at: string };
export type Ranking = {
  supplier_id: number;
  rank: number;
  score: number;
  tier: "local" | "delivers" | "neighbor" | "nationwide" | "other" | "unknown" | "any";
  tier_label: string;
  breakdown: { relevance: number; location: number; completeness: number; reliability: number };
  breakdown_max: { relevance: number; location: number; completeness: number; reliability: number };
  reasons: string[];
  missing: string[];
  questions: string[];
  warnings: Warning[];
  recommended: boolean;
  /** What confirmed the product (absent for old API responses). */
  evidence?: { kind: string; phrase: string | null; where: string | null; text: string | null; label: string };
};
export type Warning = { code: string; severity: "warn" | "info"; text: string };

/** Structured price, derived on the server from the raw strings; `display` is ready to show. */
export type Price = {
  item_name: string | null;
  pack_value: number | null;
  pack_unit: string | null;
  price_rub: number | null;
  price_per_base_unit: number | null;
  base_unit: "kg" | "l" | "pcs" | null;
  raw: string;
  source_ref: string;
  needs_confirmation: boolean;
  note: string | null;
  display: string;
  matches_product: boolean;
};
export type MinOrder = { value: number | null; unit: "RUB" | "kg" | "pcs" | null; raw: string; source_ref: string; display: string; note: string | null };
export type Commerce = { price_headline: Price | null; prices_other: Price[]; min_order: MinOrder | null };
export type SupplierData = {
  id: number;
  domain: string;
  website: string;
  name: string;
  inn: string | null;
  note: string | null;
  research: {
    pages: string[];
    missing: string[];
    researched_at: string;
    qualification?: { supplier: string; product: string; reason: string };
  } | null;
  fields: Record<string, FieldValue[]>;
  ranking: Ranking;
  /** Optional: absent in old responses; the UI then falls back to the raw field strings. */
  commerce?: Commerce;
  /** Work status within this search; absent in old responses (treated as "new"). */
  status?: { value: SupplierStatus; updated_at: string | null };
  /** When the supplier's site was crawled (the search finish date for old records). */
  collected_at?: string | null;
};
export type SupplierStatus = "new" | "called" | "awaiting_price" | "shortlisted" | "declined";
/** Optional search requirements as stored by the server. */
export type Extras = {
  volume?: { value: number; unit: string };
  documents?: ("haccp" | "iso" | "mercury")[];
  storage?: "chilled" | "frozen";
  delivery?: string;
};
/** Optional requirements as sent by the form. */
export type ExtrasInput = {
  volume_value?: number | null;
  volume_unit?: "кг" | "т" | "л" | "шт" | null;
  documents?: ("haccp" | "iso" | "mercury")[];
  storage?: "chilled" | "frozen" | null;
  delivery?: string | null;
};
/** A catalog category or a free-text product. */
export type ProductInput = { category_id: number | null; product_text: string | null };

export type RunRequest = ProductInput & {
  location_kind: "city" | "region" | null;
  location_key: string | null;
  include_neighbors: boolean;
  supplier_type: string | null;
  extras?: ExtrasInput | null;
};

/** Запускает поиск. Возвращает id запуска или текст ошибки (например, «уже идут другие запуски»). */
export async function createRun(body: RunRequest): Promise<{ runId: number } | { error: string }> {
  const response = await apiFetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (response.ok) return { runId: ((await response.json()) as { run_id: number }).run_id };
  const detail = ((await response.json().catch(() => ({}))) as { detail?: string }).detail;
  return { error: detail ?? `Не удалось запустить поиск (HTTP ${response.status})` };
}

/** Several products with the same place, type and requirements: separate searches that run one after another. */
export async function createRuns(body: Omit<RunRequest, "category_id" | "product_text"> & { products: ProductInput[] }): Promise<{ runIds: number[]; groupId: number | null } | { error: string }> {
  const response = await apiFetch("/api/runs/batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (response.ok) {
    const data = (await response.json()) as { run_ids: number[]; group_id: number | null };
    return { runIds: data.run_ids, groupId: data.group_id };
  }
  const detail = ((await response.json().catch(() => ({}))) as { detail?: string }).detail;
  return { error: detail ?? `Не удалось запустить поиск (HTTP ${response.status})` };
}

export async function fetchGroup(groupId: number): Promise<Run[]> {
  const response = await apiFetch(`/api/runs/group/${groupId}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return ((await response.json()) as { runs: Run[] }).runs;
}

export async function fetchLimits(): Promise<{ max_products: number }> {
  const response = await apiFetch("/api/runs/limits");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as { max_products: number };
}

export async function fetchRun(id: number): Promise<Run> {
  const response = await apiFetch(`/api/runs/${id}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as Run;
}

export async function fetchRuns(limit = 200): Promise<Run[]> {
  const response = await apiFetch(`/api/runs?limit=${limit}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return ((await response.json()) as { runs: Run[] }).runs;
}

/** Последний такой же поиск (категория, место, соседи, тип поставщика), если он уже был и не завершился ошибкой. */
export async function fetchPreviousRun(body: RunRequest): Promise<Run | null> {
  const params = new URLSearchParams({ include_neighbors: String(body.include_neighbors) });
  if (body.category_id !== null) params.set("category_id", String(body.category_id));
  else if (body.product_text) params.set("product_text", body.product_text);
  if (body.location_kind && body.location_key) {
    params.set("location_kind", body.location_kind);
    params.set("location_key", body.location_key);
  }
  if (body.supplier_type) params.set("supplier_type", body.supplier_type);
  const response = await apiFetch(`/api/runs/previous?${params}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return ((await response.json()) as { run: Run | null }).run;
}

export async function fetchRunSuppliers(id: number): Promise<SupplierData[]> {
  const response = await apiFetch(`/api/runs/${id}/suppliers`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return ((await response.json()) as { suppliers: SupplierData[] }).suppliers;
}

/** Живая лента шагов агента (SSE). Повторяющиеся события при переподключении отбрасываются по номеру. */
export function subscribeRun(runId: number, onEvent: (event: RunEvent) => void, onEnd: (status: string) => void): () => void {
  const source = new EventSource(`/api/runs/${runId}/stream`);
  let last = 0;
  source.onmessage = (message) => {
    const event = JSON.parse(message.data) as RunEvent;
    if (event.type === "stream_end") {
      source.close();
      onEnd(String(event.status ?? ""));
      return;
    }
    if (event.seq !== undefined) {
      if (event.seq <= last) return;
      last = event.seq;
    }
    onEvent(event);
  };
  return () => source.close();
}

/** Сохраняет заметку о поставщике; пустая строка удаляет заметку. */
export async function saveSupplierNote(supplierId: number, note: string): Promise<string | null> {
  const response = await apiFetch(`/api/suppliers/${supplierId}/note`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return ((await response.json()) as { note: string | null }).note;
}

/** Sets the work status of a supplier within one search. */
export async function setSupplierStatus(runId: number, supplierId: number, status: SupplierStatus): Promise<{ value: SupplierStatus; updated_at: string }> {
  const response = await apiFetch(`/api/runs/${runId}/suppliers/${supplierId}/status`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as { value: SupplierStatus; updated_at: string };
}

/** Download address of the export; the session cookie authorizes a plain link. `ids` fixes the suppliers and their order. */
export function exportUrl(runId: number, format: "xlsx" | "csv", ids: number[]): string {
  const params = new URLSearchParams({ format });
  if (ids.length > 0) params.set("ids", ids.join(","));
  return `/api/runs/${runId}/export?${params}`;
}
