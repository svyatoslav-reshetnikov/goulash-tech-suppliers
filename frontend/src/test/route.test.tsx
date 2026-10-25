import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { parseRoute } from "../route";
import fixture from "./fixtures/search-rice-moscow.json";
import { CATEGORIES, RUN, mockApi, stubEventSource } from "./mockApi";

describe("parseRoute", () => {
  it.each([
    ["", { tab: "search", runId: null, product: null }],
    ["#search", { tab: "search", runId: null, product: null }],
    ["#search/7", { tab: "search", runId: 7, product: null }],
    ["#search?product=36", { tab: "search", runId: null, product: 36 }],
    ["#history", { tab: "history", runId: null, product: null }],
    ["#history/12", { tab: "history", runId: 12, product: null }],
    ["#catalog", { tab: "catalog", runId: null, product: null }],
    ["#history/abc", { tab: "history", runId: null, product: null }],
    ["#nonsense/3", { tab: "search", runId: 3, product: null }],
    ["#search?product=x", { tab: "search", runId: null, product: null }],
  ])("%s", (hash, expected) => {
    expect(parseRoute(hash)).toEqual(expected);
  });
});

describe("opening or reloading a page by its address restores the matching view", () => {
  beforeEach(() => {
    stubEventSource();
    mockApi({
      "/api/auth/me": () => ({ auth_required: false, authenticated: true, user: null }),
      "/api/health": () => ({ status: "ok", database: "ok" }),
      "/api/categories": () => CATEGORIES,
      "/api/runs/limits": () => ({ max_products: 3 }),
      "/api/runs/1": () => RUN,
      "/api/runs/1/suppliers": () => ({ suppliers: fixture.suppliers, recommended_ids: [] }),
      "/api/runs": () => ({ runs: [RUN] }),
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.location.hash = "";
  });

  it("#history/<id> shows that result, not the search form", async () => {
    window.location.hash = "#history/1";
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Найденные поставщики (6)" })).toBeInTheDocument();
    expect(screen.getByText("← К истории поисков")).toBeInTheDocument();
    expect(screen.queryByText("Что закупаем")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "История поисков" })).toBeInTheDocument();
  });

  it("#history shows the list of searches", async () => {
    window.location.hash = "#history";
    render(<App />);
    expect(await screen.findByRole("heading", { name: "История поисков (1)" })).toBeInTheDocument();
  });

  it("#search shows the form and #search/<id> shows the form with that result below", async () => {
    window.location.hash = "#search/1";
    render(<App />);
    expect(await screen.findByText("Что закупаем")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Найденные поставщики (6)" })).toBeInTheDocument();
  });

  it("#search?product=<id> opens the form with that product chosen", async () => {
    window.location.hash = "#search?product=36";
    render(<App />);
    const chosen = await screen.findByRole("list", { name: "Выбранные товары" });
    expect(chosen).toHaveTextContent("Моцарелла");
  });
});
