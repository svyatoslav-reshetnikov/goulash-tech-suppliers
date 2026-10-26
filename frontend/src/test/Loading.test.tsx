import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import LocationPicker from "../components/LocationPicker";
import ProductPicker from "../components/ProductPicker";
import { CATEGORIES } from "./mockApi";

const KAZAN = { kind: "city", key: "RU-TA|Казань", label: "Казань", region_id: "RU-TA", region_name: "Республика Татарстан", population: 1300000 };
const RESOLVED = {
  selected: KAZAN,
  regions: [
    { id: "RU-TA", name: "Республика Татарстан", role: "primary" },
    { id: "RU-UD", name: "Удмуртская Республика", role: "neighbor" },
    { id: "RU-CU", name: "Чувашская Республика", role: "neighbor" },
  ],
};

/** fetch whose answers wait until the test releases them: the moment between a click and the answer is what we check. */
function slowApi(answers: Record<string, unknown>) {
  const pending: { path: string; release: () => void }[] = [];
  const calls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      const url = new URL(input, "http://localhost");
      calls.push(url);
      return new Promise<Response>((resolve) => {
        pending.push({ path: url.pathname, release: () => resolve(new Response(JSON.stringify(answers[url.pathname]), { status: 200 })) });
      });
    }),
  );
  const release = async (path: string) => {
    await act(async () => {
      for (const p of pending.filter((x) => x.path === path)) p.release();
      pending.splice(0, pending.length, ...pending.filter((x) => x.path !== path));
    });
  };
  return { calls, release };
}

afterEach(() => vi.unstubAllGlobals());

describe("no «nothing here» while an answer is on its way", () => {
  it("product list says it is loading until the catalog arrives", async () => {
    const api = slowApi({ "/api/categories": CATEGORIES });
    const user = userEvent.setup();
    render(<ProductPicker onChange={() => undefined} />);
    await user.click(screen.getByRole("combobox", { name: /Что закупаем/ }));
    expect(screen.getByText("Загружаем справочник…")).toBeInTheDocument();
    expect(screen.queryByText("В справочнике такой позиции нет")).not.toBeInTheDocument();
    await api.release("/api/categories");
    expect(await screen.findByRole("button", { name: /^Моцарелла/ })).toBeInTheDocument();
  });

  it("a product from a catalog link shows loading, never an empty field, until it is chosen", async () => {
    const api = slowApi({ "/api/categories": CATEGORIES });
    render(<ProductPicker onChange={() => undefined} initialCategoryId={36} />);
    expect(screen.getByText("Загружаем справочник…")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /Что закупаем/ })).not.toBeInTheDocument();
    await api.release("/api/categories");
    expect(await screen.findByRole("button", { name: "Убрать: Моцарелла" })).toBeInTheDocument();
  });

  it("place suggestions say «Ищем…» instead of «Ничего не найдено» while searching", async () => {
    const api = slowApi({ "/api/locations": { results: [KAZAN] } });
    const user = userEvent.setup();
    render(<LocationPicker />);
    await user.type(screen.getByRole("combobox"), "каз");
    expect(screen.getByText("Ищем…")).toBeInTheDocument();
    expect(screen.queryByText("Ничего не найдено")).not.toBeInTheDocument();
    await screen.findByText("Ищем…"); // the request leaves after a short pause
    await vi.waitFor(() => expect(api.calls.some((c) => c.pathname === "/api/locations")).toBe(true));
    await api.release("/api/locations");
    expect(await screen.findByRole("button", { name: /Казань/ })).toBeInTheDocument();
  });

  it("neighbours are loaded with the region: the checkbox shows them at once and never flashes «соседних регионов нет»", async () => {
    const api = slowApi({ "/api/locations": { results: [KAZAN] }, "/api/locations/resolve": RESOLVED });
    const user = userEvent.setup();
    render(<LocationPicker />);
    await user.type(screen.getByRole("combobox"), "каз");
    await vi.waitFor(() => expect(api.calls.some((c) => c.pathname === "/api/locations")).toBe(true));
    await api.release("/api/locations");
    await user.click(await screen.findByRole("button", { name: /Казань/ }));

    await user.click(screen.getByRole("checkbox", { name: "Включая соседние регионы" })); // before the server answered
    expect(screen.getByText("Загружаем соседние регионы…")).toBeInTheDocument();
    expect(screen.queryByText(/соседних регионов нет/)).not.toBeInTheDocument();
    await api.release("/api/locations/resolve");
    expect(screen.getByText(/и соседние \(2\)/)).toBeInTheDocument();
    expect(screen.getByText("Удмуртская Республика")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "Включая соседние регионы" })); // off and on again: no new request, no flash
    expect(screen.queryByText("Удмуртская Республика")).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Включая соседние регионы" }));
    expect(screen.getByText("Удмуртская Республика")).toBeInTheDocument();
    const resolves = api.calls.filter((c) => c.pathname === "/api/locations/resolve");
    expect(resolves).toHaveLength(1);
    expect(resolves[0].searchParams.get("neighbors")).toBe("true");
  });
});
