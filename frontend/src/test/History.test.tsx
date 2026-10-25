import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Run } from "../api";
import CategoriesTree from "../components/CategoriesTree";
import HistoryPage, { groupRuns } from "../components/HistoryPage";
import { CATEGORIES, RUN, mockApi, stubEventSource } from "./mockApi";

const run = (id: number, extra: Partial<Run> = {}): Run => ({ ...(RUN as unknown as Run), id, category: `Товар ${id}`, ...extra });

afterEach(() => vi.unstubAllGlobals());

describe("history groups", () => {
  it("collects consecutive runs of one multi-product search and leaves single runs alone", () => {
    const runs = [run(9), run(8, { group_id: 6 }), run(7, { group_id: 6 }), run(6, { group_id: 6 }), run(5)];
    expect(groupRuns(runs).map((g) => g.map((r) => r.id))).toEqual([[9], [8, 7, 6], [5]]);
  });

  it("shows a group under one heading, in the order the products were launched", async () => {
    stubEventSource();
    mockApi({ "/api/runs": () => ({ runs: [run(8, { group_id: 6 }), run(7, { group_id: 6 }), run(6, { group_id: 6, free_text: true }), run(5)] }) });
    render(<HistoryPage runId={null} onOpen={() => undefined} onBack={() => undefined} />);
    expect(await screen.findByText("Поиск по 3 товарам · Товар 6, Товар 7, Товар 8")).toBeInTheDocument();
    const group = screen.getByText(/Поиск по 3 товарам/).closest("li") as HTMLElement;
    expect(within(group).getAllByRole("button")).toHaveLength(3);
    expect(within(group).getByText("свой товар")).toBeInTheDocument();
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
