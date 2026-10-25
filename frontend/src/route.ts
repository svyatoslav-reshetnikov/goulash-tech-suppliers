export type Route = { tab: "search" | "history" | "catalog"; runId: number | null; product: number | null };

/**
 * Hash routes: `#search`, `#search/<run>`, `#search?product=<category>` (form prefilled from the catalog),
 * `#history`, `#history/<run>`, `#catalog`. Anything unknown opens the search form.
 */
export function parseRoute(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#/, "").split("?");
  const [tab, id] = path.split("/");
  const product = new URLSearchParams(query).get("product") ?? "";
  return {
    tab: tab === "catalog" ? "catalog" : tab === "history" ? "history" : "search",
    runId: id && /^\d+$/.test(id) ? Number(id) : null,
    product: /^\d+$/.test(product) ? Number(product) : null,
  };
}
