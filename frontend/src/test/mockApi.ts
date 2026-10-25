import { vi } from "vitest";

type Handler = (init: RequestInit | undefined, url: URL) => unknown;

/** Replaces fetch with a tiny router: keys are "GET /api/path" (or just "/api/path" for any method). Unknown paths answer 404. */
export function mockApi(handlers: Record<string, Handler>) {
  const calls: { method: string; path: string; body: unknown; query: URLSearchParams }[] = [];
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, "http://localhost");
    const method = init?.method ?? "GET";
    calls.push({ method, path: url.pathname, body: init?.body ? JSON.parse(String(init.body)) : undefined, query: url.searchParams });
    const handler = handlers[`${method} ${url.pathname}`] ?? handlers[url.pathname];
    if (!handler) return new Response(JSON.stringify({ detail: "not mocked" }), { status: 404 });
    return new Response(JSON.stringify(handler(init, url)), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

/** EventSource that reports the end of the stream at once (finished runs). */
export function stubEventSource() {
  class FakeEventSource {
    onmessage: ((message: { data: string }) => void) | null = null;
    constructor() {
      setTimeout(() => this.onmessage?.({ data: JSON.stringify({ type: "stream_end", status: "done" }) }), 0);
    }
    close() {}
  }
  vi.stubGlobal("EventSource", FakeEventSource);
}

export const CATEGORIES = {
  total: 3,
  groups: [
    {
      id: 1,
      name: "Молочные продукты и сыры",
      kind: "ingredient",
      categories: [
        { id: 36, name: "Моцарелла", synonyms: ["Сыр моцарелла"], origin: "derived", items: 5 },
        { id: 37, name: "Сливочный сыр", synonyms: [], origin: "derived", items: 3 },
      ],
    },
    { id: 2, name: "Рыба и морепродукты", kind: "ingredient", categories: [{ id: 40, name: "Лосось", synonyms: [], origin: "derived", items: 9 }] },
  ],
};

export const RUN = {
  id: 1, status: "done", stop_reason: "target_reached", category: "Рис для суши", free_text: false, group_id: null, extras: null,
  location: "Москва", include_neighbors: false, regions: [{ id: "RU-MOW", name: "Москва", role: "primary" }], supplier_type: null,
  summary: null, error: null, stats: { suppliers_saved: 6, cost_rub: 76.8 }, created_at: "2026-10-24T19:00:00Z", started_at: null, finished_at: "2026-10-24T19:02:00Z",
};
