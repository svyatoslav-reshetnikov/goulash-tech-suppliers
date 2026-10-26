import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SupplierData } from "../api";
import ResultsPanel from "../components/ResultsPanel";
import fixture from "./fixtures/search-rice-moscow.json";

// Stored result of the "Рис для суши", Москва search in the shape the API returns: only suppliers with a confirmed product.
const suppliers = fixture.suppliers as unknown as SupplierData[];
// the comparison takes up to 4: two more copies of a real supplier give enough cards to hit the limit
const extended = [...suppliers, ...[4, 5].map((n) => ({ ...suppliers[1], id: 100 + n, name: `Поставщик ${n}`, domain: `s${n}.test` }))];

function setup(runId = 1, list: SupplierData[] = suppliers) {
  const user = userEvent.setup();
  const view = render(<ResultsPanel runId={runId} suppliers={list} live={false} onNote={() => undefined} />);
  return { user, ...view };
}

const card = (name: string) => screen.getByRole("article", { name });
const toggle = (name: string) => within(card(name)).getByRole("button", { name: new RegExp(`^(Развернуть|Свернуть): ${name}$`) });

describe("collapsed cards", () => {
  it("shows one compact card per supplier with price, contacts, region and what to ask", () => {
    setup();
    expect(screen.getAllByRole("article")).toHaveLength(3);
    const suomi = within(card("SuomiFish"));
    expect(suomi.getByText(/≈84 ₽\/кг/)).toBeInTheDocument();
    expect(suomi.getByText(/Уточните цену/)).toBeInTheDocument();
    expect(suomi.getByRole("link", { name: "+7 (495) 988-64-94" })).toHaveAttribute("href", "tel:+74959886494");
    expect(suomi.getByRole("link", { name: "info@suomifish.ru" })).toHaveAttribute("href", "mailto:info@suomifish.ru");
    expect(suomi.getByText("Рекомендуем связаться")).toBeInTheDocument();
    expect(within(card("Сушимаг")).queryByText("Рекомендуем связаться")).not.toBeInTheDocument();
    expect(card("ТД Фиш Сити")).toHaveTextContent("В соседнем регионе: Московская область");
    expect(document.body.textContent).not.toMatch(/доставляют ли/); // про доставку в регион поиска не спрашиваем
    expect(document.body.textContent).not.toMatch(/Товар не подтверждён/);
  });

  it("has no work statuses", () => {
    setup();
    expect(screen.queryByRole("combobox", { name: /Статус/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Позвонили|Новый/)).not.toBeInTheDocument();
  });

  it("expands one card with an arrow (aria-expanded) and remembers it per search across remounts", async () => {
    const { user, unmount } = setup();
    expect(toggle("SuomiFish")).toHaveAttribute("aria-expanded", "false");
    expect(toggle("SuomiFish")).not.toHaveTextContent(/Подробнее/);
    await user.click(toggle("SuomiFish"));
    expect(toggle("SuomiFish")).toHaveAttribute("aria-expanded", "true");
    expect(within(card("SuomiFish")).getByText(/Совпадение с продуктом/)).toBeInTheDocument();
    unmount(); // switching tabs unmounts the page
    const again = setup();
    expect(toggle("SuomiFish")).toHaveAttribute("aria-expanded", "true");
    expect(toggle("Сушимаг")).toHaveAttribute("aria-expanded", "false");
    again.unmount();
    setup(2); // another search has its own state
    expect(toggle("SuomiFish")).toHaveAttribute("aria-expanded", "false");
  });

  it("expands and collapses everything", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Развернуть все" }));
    expect(screen.getAllByRole("button", { name: /^Свернуть: / })).toHaveLength(3);
    await user.click(screen.getByRole("button", { name: "Свернуть все" }));
    expect(screen.getAllByRole("button", { name: /^Развернуть: / })).toHaveLength(3);
  });
});

describe("sources toggle", () => {
  it("hides source links by default and shows them inline when switched on", async () => {
    const { user } = setup();
    await user.click(toggle("SuomiFish"));
    expect(screen.queryAllByRole("link", { name: "источник" })).toHaveLength(0);
    await user.click(screen.getByRole("checkbox", { name: "Показывать источники" }));
    const links = within(card("SuomiFish")).getAllByRole("link", { name: "источник" });
    expect(links.length).toBeGreaterThan(3);
    expect(links.every((a) => /^https?:\/\//.test(a.getAttribute("href") ?? ""))).toBe(true);
  });
});

describe("table view", () => {
  async function table() {
    const view = setup();
    await view.user.click(screen.getByRole("button", { name: "Таблица" }));
    return view;
  }
  const order = () => within(screen.getAllByRole("rowgroup")[1]).getAllByRole("row").map((r) => within(r).getAllByRole("cell")[2].textContent?.replace("рекомендуем", "").trim());

  it("lists suppliers by rank and sorts by score, price per base unit and minimum order", async () => {
    const { user } = await table();
    expect(order()).toEqual(["SuomiFish", "Сушимаг", "ТД Фиш Сити"]);
    await user.click(screen.getByRole("button", { name: /^Оценка/ }));
    expect(order()[0]).toBe("SuomiFish"); // best score first
    await user.click(screen.getByRole("button", { name: /^Оценка/ }));
    expect(order()[2]).toBe("SuomiFish");
    await user.click(screen.getByRole("button", { name: /^Цена на товар/ }));
    expect(order()[0]).toBe("SuomiFish"); // the only supplier with a price: others (no price) stay last
    await user.click(screen.getByRole("button", { name: /^Цена на товар/ }));
    expect(order()[0]).toBe("SuomiFish"); // no price is always last, whatever the direction
    await user.click(screen.getByRole("button", { name: /^Мин\. заказ/ }));
    expect(order()[0]).toBe("Сушимаг"); // the only minimum order: the others have none
  });

  it("shows headline price per unit and warning counts", async () => {
    await table();
    expect(within(screen.getByRole("row", { name: /SuomiFish/ })).getByText(/≈84 ₽\/кг/)).toBeInTheDocument();
    expect(within(screen.getByRole("row", { name: /Сушимаг/ })).getByTitle(/Регион неизвестен/)).toBeInTheDocument();
  });
});

describe("comparison (regression: selecting suppliers and pressing «Сравнить» opens the comparison)", () => {
  const pick = async (user: ReturnType<typeof userEvent.setup>, names: string[]) => {
    for (const name of names) await user.click(screen.getByRole("checkbox", { name: `Сравнить: ${name}` }));
  };

  it.each([
    [2, ["SuomiFish", "Сушимаг"]],
    [3, ["SuomiFish", "Сушимаг", "ТД Фиш Сити"]],
    [4, ["SuomiFish", "Сушимаг", "ТД Фиш Сити", "Поставщик 4"]],
  ])("opens for %i selected suppliers and keeps the selection", async (count, names) => {
    const { user } = setup(1, extended);
    await pick(user, names);
    expect(screen.getByText(new RegExp(`Для сравнения выбрано: ${count} из 4`))).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Сравнить" }));
    expect(screen.getByRole("heading", { name: `Сравнение (${count})` })).toBeInTheDocument();
    for (const name of names) expect(screen.getByRole("checkbox", { name: `Сравнить: ${name}` })).toBeChecked();
  });

  it("needs two suppliers, limits the selection to four and can be reset", async () => {
    const { user } = setup(1, extended);
    await pick(user, ["SuomiFish"]);
    expect(screen.getByRole("button", { name: "Сравнить" })).toBeDisabled();
    await pick(user, ["Сушимаг", "ТД Фиш Сити", "Поставщик 4"]);
    expect(screen.getByRole("checkbox", { name: "Сравнить: Поставщик 5" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Сбросить" }));
    expect(screen.queryByText(/Для сравнения выбрано/)).not.toBeInTheDocument();
  });

  it("works from the table view as well and a supplier can be removed from the comparison", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Таблица" }));
    await pick(user, ["SuomiFish", "Сушимаг"]);
    await user.click(screen.getByRole("button", { name: "Сравнить" }));
    expect(screen.getByRole("heading", { name: "Сравнение (2)" })).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "убрать из сравнения" })[0]);
    expect(screen.queryByRole("heading", { name: /Сравнение \(/ })).not.toBeInTheDocument();
  });
});


/** Query of an export link: format and the supplier ids in the requested order. */
function exported(link: HTMLElement): { format: string | null; ids: string } {
  const url = new URL(link.getAttribute("href") ?? "", "http://localhost");
  return { format: url.searchParams.get("format"), ids: url.searchParams.get("ids") ?? "" };
}

describe("freshness and export", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("exports all suppliers in rank order by default, the chosen ones when some are selected, and follows the table sort", async () => {
    const { user } = setup();
    const ids = (list: SupplierData[]) => list.map((s) => s.id).join(",");
    expect(exported(screen.getByRole("link", { name: /Скачать в Excel/ }))).toEqual({ format: "xlsx", ids: ids(suppliers) });
    await user.click(screen.getByRole("checkbox", { name: "Сравнить: Сушимаг" }));
    await user.click(screen.getByRole("checkbox", { name: "Сравнить: SuomiFish" }));
    const chosen = suppliers.filter((s) => ["SuomiFish", "Сушимаг"].includes(s.name));
    expect(screen.getByRole("link", { name: /Скачать в Excel/ })).toHaveAccessibleName("Скачать в Excel (выбранные: 2)");
    expect(exported(screen.getByRole("link", { name: /Скачать в Excel/ }))).toEqual({ format: "xlsx", ids: ids(chosen) });
    expect(screen.getByRole("link", { name: "CSV" })).toHaveAttribute("href", expect.stringContaining("format=csv"));
    await user.click(screen.getByRole("button", { name: "Сбросить" }));
    await user.click(screen.getByRole("button", { name: "Таблица" }));
    await user.click(screen.getByRole("button", { name: /^Мин\. заказ/ }));
    const first = suppliers.find((s) => s.name === "Сушимаг")!;
    expect(exported(screen.getByRole("link", { name: /Скачать в Excel/ })).ids.split(",")[0]).toBe(String(first.id));
  });

  it("shows the date the data was collected on the card", () => {
    setup();
    const dated = suppliers.filter((s) => s.collected_at);
    expect(dated.length).toBeGreaterThan(0);
    expect(within(card(dated[0].name)).getByText(/Данные собраны: \d{2}\.\d{2}\.\d{4}/)).toBeInTheDocument();
  });
});
