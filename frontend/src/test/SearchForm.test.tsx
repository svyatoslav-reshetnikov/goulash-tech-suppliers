import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SearchForm from "../components/SearchForm";
import { CATEGORIES, RUN, mockApi } from "./mockApi";

function setup(handlers: Parameters<typeof mockApi>[0] = {}) {
  const api = mockApi({
    "/api/categories": () => CATEGORIES,
    "GET /api/runs/previous": () => ({ run: null }),
    ...handlers,
  });
  const onStarted = vi.fn();
  const onOpenPrevious = vi.fn();
  const user = userEvent.setup();
  render(<SearchForm onStarted={onStarted} onOpenPrevious={onOpenPrevious} busy={false} />);
  return { api, user, onStarted, onOpenPrevious };
}

const input = () => screen.getByRole("combobox", { name: /Что закупаем/ });
async function choose(user: ReturnType<typeof userEvent.setup>, query: string, name: string) {
  await user.click(input());
  await user.type(input(), query);
  await user.click(await screen.findByRole("button", { name: new RegExp(`^${name}`) })); // the option also names its group
}

afterEach(() => vi.unstubAllGlobals());

describe("product", () => {
  it("is one item strictly from the catalog and the search sends only its id and the place", async () => {
    const { api, user, onStarted } = setup({ "POST /api/runs": () => ({ run_id: 9 }) });
    await choose(user, "моц", "Моцарелла");
    expect(screen.queryByRole("combobox", { name: /Что закупаем/ })).not.toBeInTheDocument(); // one product: the field gives way to the chip
    expect(screen.getByRole("button", { name: "Убрать: Моцарелла" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Найти поставщиков" }));
    const create = api.calls.find((c) => c.method === "POST" && c.path === "/api/runs");
    expect(create?.body).toEqual({ category_id: 36, location_kind: null, location_key: null, include_neighbors: false });
    expect(onStarted).toHaveBeenCalledWith(9);
  });

  it("does not offer a product that is not in the catalog", async () => {
    const { user } = setup();
    await user.click(input());
    await user.type(input(), "Мука пшеничная");
    expect(await screen.findByText("В справочнике такой позиции нет")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /искать как есть/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Найти поставщиков" })).toBeDisabled();
  });

  it("has no supplier type filter, no «Дополнительно» block and no price of the search", () => {
    setup();
    expect(screen.queryByText("Тип поставщика")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Дополнительно/ })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/₽/);
  });
});

describe("duplicate search warning", () => {
  it("warns «Такой поиск уже осуществлялся» with two choices", async () => {
    const { user, onOpenPrevious } = setup({ "GET /api/runs/previous": () => ({ run: RUN }) });
    await choose(user, "моц", "Моцарелла");
    await user.click(screen.getByRole("button", { name: "Найти поставщиков" }));
    expect(await screen.findByText("Такой поиск уже осуществлялся")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Посмотреть результаты предыдущего поиска" }));
    expect(onOpenPrevious).toHaveBeenCalledWith(1);
  });
});

describe("form clarity", () => {
  it("«Вся Россия» is a toggle chip with a selected state and neighbours show a hint until a region is chosen", () => {
    setup();
    expect(screen.getByRole("button", { name: "Вся Россия" })).toHaveAttribute("aria-pressed", "true");
    const neighbours = screen.getByRole("checkbox", { name: "Включая соседние регионы" });
    expect(neighbours).toBeDisabled();
    expect(screen.getByText("Сначала выберите регион")).toBeInTheDocument();
    expect(neighbours).toHaveAccessibleDescription("Сначала выберите регион");
  });
});
