import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SearchForm from "../components/SearchForm";
import { estimateText, plural } from "../lib/estimate";
import { CATEGORIES, RUN, mockApi } from "./mockApi";

function setup(handlers: Parameters<typeof mockApi>[0] = {}) {
  const api = mockApi({
    "/api/categories": () => CATEGORIES,
    "/api/runs/limits": () => ({ max_products: 3 }),
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
async function addCatalog(user: ReturnType<typeof userEvent.setup>, query: string, name: string) {
  await user.click(screen.getByRole("combobox", { name: /Что закупаем/ }));
  await user.type(screen.getByRole("combobox", { name: /Что закупаем/ }), query);
  await user.click(await screen.findByRole("button", { name: new RegExp(`^${name}`) })); // the option also names its group
}

afterEach(() => vi.unstubAllGlobals());

describe("estimate text", () => {
  it("matches the wording of the specification", () => {
    expect(estimateText(3)).toBe("Будет запущено 3 поиска, ≈ 210–300 ₽, 6–12 минут");
    expect(estimateText(2)).toBe("Будет запущено 2 поиска, ≈ 140–200 ₽, 4–8 минут");
    expect(estimateText(5)).toBe("Будет запущено 5 поисков, ≈ 350–500 ₽, 10–20 минут");
    expect(plural(21, "поиск", "поиска", "поисков")).toBe("поиск");
    expect(plural(11, "поиск", "поиска", "поисков")).toBe("поисков");
  });
});

describe("free-text product", () => {
  it("offers «искать как есть» when nothing matches and searches with product_text and no category", async () => {
    const { api, user, onStarted } = setup({ "POST /api/runs": () => ({ run_id: 9 }) });
    await user.click(input());
    await user.type(input(), "Мука пшеничная");
    await user.click(await screen.findByRole("button", { name: /Такой позиции нет в справочнике — искать как есть: «Мука пшеничная»/ }));
    expect(within(screen.getByRole("list", { name: "Выбранные товары" })).getByText(/Мука пшеничная/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Найти поставщиков" }));
    const create = api.calls.find((c) => c.method === "POST" && c.path === "/api/runs");
    expect(create?.body).toMatchObject({ category_id: null, product_text: "Мука пшеничная", location_kind: null, include_neighbors: false });
    expect(onStarted).toHaveBeenCalledWith(9);
  });

  it("does not offer it for a one-letter text or when a catalog item matches exactly", async () => {
    const { user } = setup();
    await user.click(input());
    await user.type(input(), "я");
    expect(screen.queryByRole("button", { name: /искать как есть/ })).not.toBeInTheDocument();
    await user.clear(input());
    await user.type(input(), "моцарелла");
    expect(screen.queryByRole("button", { name: /искать как есть/ })).not.toBeInTheDocument();
  });
});

describe("several products", () => {
  it("asks for confirmation with cost and time, then launches the group and opens the first search", async () => {
    const { api, user, onStarted } = setup({ "POST /api/runs/batch": () => ({ run_ids: [11, 12, 13], group_id: 11 }) });
    await addCatalog(user, "моц", "Моцарелла");
    await addCatalog(user, "лос", "Лосось");
    await user.click(input());
    await user.type(input(), "Угорь копчёный");
    await user.click(await screen.findByRole("button", { name: /искать как есть/ }));
    expect(screen.queryByRole("combobox", { name: /Что закупаем/ })).not.toBeInTheDocument(); // three products chosen: the field is replaced by the "maximum" note
    expect(screen.getByText(/Выбрано максимум товаров \(3\)/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Найти поставщиков \(3 товара\)/ }));
    const dialog = await screen.findByRole("alertdialog", { name: "Подтверждение запуска" });
    expect(dialog).toHaveTextContent("Будет запущено 3 поиска, ≈ 210–300 ₽, 6–12 минут");
    expect(api.calls.some((c) => c.path === "/api/runs/batch")).toBe(false); // nothing is launched before the confirmation
    await user.click(within(dialog).getByRole("button", { name: "Запустить 3" }));
    const batch = api.calls.find((c) => c.path === "/api/runs/batch");
    expect(batch?.body).toMatchObject({ products: [{ category_id: 36, product_text: null }, { category_id: 40, product_text: null }, { category_id: null, product_text: "Угорь копчёный" }] });
    expect(onStarted).toHaveBeenCalledWith(11);
  });

  it("lists products already searched and can launch only the new ones", async () => {
    const { api, user } = setup({
      "GET /api/runs/previous": (_init, url) => ({ run: url.searchParams.get("category_id") === "36" ? RUN : null }),
      "POST /api/runs": () => ({ run_id: 21 }),
    });
    await addCatalog(user, "моц", "Моцарелла");
    await addCatalog(user, "лос", "Лосось");
    await user.click(screen.getByRole("button", { name: /Найти поставщиков \(2 товара\)/ }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Уже искали: Моцарелла");
    await user.click(within(dialog).getByRole("button", { name: "Только новые (1)" }));
    expect(api.calls.find((c) => c.method === "POST" && c.path === "/api/runs")?.body).toMatchObject({ category_id: 40 });
  });

  it("limits the number of products to the server setting", async () => {
    const { user } = setup({ "/api/runs/limits": () => ({ max_products: 1 }) });
    await addCatalog(user, "моц", "Моцарелла");
    expect(await screen.findByText(/Выбрано максимум товаров \(1\)/)).toBeInTheDocument();
  });
});

describe("duplicate search warning", () => {
  it("warns «Такой поиск уже осуществлялся» with two choices", async () => {
    const { user, onOpenPrevious } = setup({ "GET /api/runs/previous": () => ({ run: RUN }) });
    await addCatalog(user, "моц", "Моцарелла");
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

describe("«Дополнительно»", () => {
  it("is collapsed by default and its values go to the search request", async () => {
    const { api, user } = setup({ "POST /api/runs": () => ({ run_id: 5 }) });
    const toggle = screen.getByRole("button", { name: /Дополнительно/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText("Объём закупки в месяц")).not.toBeInTheDocument();
    await user.click(toggle);
    await user.type(screen.getByLabelText("Объём закупки в месяц"), "500");
    await user.click(screen.getByRole("checkbox", { name: "ХАССП" }));
    await user.click(screen.getByRole("radio", { name: "Охлаждённое" }));
    await user.type(screen.getByLabelText("Условия доставки"), "до 8 утра");
    await addCatalog(user, "моц", "Моцарелла");
    await user.click(screen.getByRole("button", { name: "Найти поставщиков" }));
    const body = api.calls.find((c) => c.method === "POST" && c.path === "/api/runs")?.body as { extras: unknown };
    expect(body.extras).toMatchObject({ volume_value: 500, documents: ["haccp"], storage: "chilled", delivery: "до 8 утра" });
  });

  it("sends no extras when nothing is filled in", async () => {
    const { api, user } = setup({ "POST /api/runs": () => ({ run_id: 5 }) });
    await addCatalog(user, "моц", "Моцарелла");
    await user.click(screen.getByRole("button", { name: "Найти поставщиков" }));
    expect((api.calls.find((c) => c.method === "POST" && c.path === "/api/runs")?.body as { extras: unknown }).extras).toBeNull();
  });
});

