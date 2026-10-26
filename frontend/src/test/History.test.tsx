import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Run } from "../api";
import CategoriesTree from "../components/CategoriesTree";
import HistoryPage from "../components/HistoryPage";
import { CATEGORIES, RUN, mockApi, stubEventSource } from "./mockApi";

const run = (id: number): Run => ({ ...(RUN as unknown as Run), id, category: `Товар ${id}` });

afterEach(() => vi.unstubAllGlobals());

describe("history", () => {
  it("lists every search on its own line", async () => {
    stubEventSource();
    mockApi({ "/api/runs": () => ({ runs: [run(8), run(7), run(6)] }) });
    render(<HistoryPage runId={null} onOpen={() => undefined} onBack={() => undefined} />);
    expect(await screen.findByText("Товар 8")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Товар \d/ })).toHaveLength(3);
  });
});

describe("catalog chips", () => {
  it("open the search form with that product", async () => {
    mockApi({ "/api/categories": () => CATEGORIES });
    render(<CategoriesTree />);
    const chip = await screen.findByRole("link", { name: /Моцарелла/ });
    expect(chip).toHaveAttribute("href", "#search?product=36");
  });
});
