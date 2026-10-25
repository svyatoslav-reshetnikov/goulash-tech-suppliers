import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SupplierData } from "../api";
import ResultsPanel from "../components/ResultsPanel";
import fixture from "./fixtures/search-rice-moscow.json";

// Stored result of the "Рис для суши", Москва search (6 suppliers), in the shape the API returns.
const suppliers = fixture.suppliers as unknown as SupplierData[];

/** The panel with the same state wiring as RunView: statuses are kept in the supplier list. */
function Harness({ runId }: { runId: number }) {
  const [list, setList] = useState(suppliers);
  return (
    <ResultsPanel
      runId={runId}
      suppliers={list}
      live={false}
      onNote={() => undefined}
      onStatus={(id, value, updatedAt) => setList((prev) => prev.map((x) => (x.id === id ? { ...x, status: { value, updated_at: updatedAt } } : x)))}
    />
  );
}

function setup(runId = 1) {
  const user = userEvent.setup();
  const view = render(<Harness runId={runId} />);
  return { user, ...view };
}

const card = (name: string) => screen.getByRole("article", { name });

describe("collapsed cards", () => {
  it("shows one compact card per supplier with price, minimum order, contacts and warnings", () => {
    setup();
    expect(screen.getAllByRole("article")).toHaveLength(6);
    const suomi = within(card("SuomiFish"));
    expect(suomi.getByText(/≈84 ₽\/кг/)).toBeInTheDocument();
    expect(suomi.getByText(/Уточните цену/)).toBeInTheDocument();
    expect(suomi.getByRole("link", { name: "+7 (495) 988-64-94" })).toHaveAttribute("href", "tel:+74959886494");
    expect(suomi.getByRole("link", { name: "info@suomifish.ru" })).toHaveAttribute("href", "mailto:info@suomifish.ru");
    expect(suomi.getByText("Рекомендуем связаться")).toBeInTheDocument();
    expect(within(card("GFC-Russia")).queryByText("Рекомендуем связаться")).not.toBeInTheDocument();
    expect(within(card("Восторг")).getByText(/Домен почты \(osttorg\.ru\) не совпадает с сайтом \(vosttorg\.ru\)/)).toBeInTheDocument();
    expect(card("GFC-Russia")).toHaveTextContent("мин. заказ: от 11 000 ₽ (также: от 1 шт.)");
  });

  it("expands one card with aria-expanded and remembers it per search across remounts", async () => {
    const { user, unmount } = setup();
    const button = within(card("SuomiFish")).getByRole("button", { name: /Подробнее/ });
    expect(button).toHaveAttribute("aria-expanded", "false");
    await user.click(button);
    expect(within(card("SuomiFish")).getByRole("button", { name: /Свернуть/ })).toHaveAttribute("aria-expanded", "true");
    expect(within(card("SuomiFish")).getByText(/Совпадение с продуктом/)).toBeInTheDocument();
    unmount(); // switching tabs unmounts the page
    const again = setup();
    expect(within(card("SuomiFish")).getByRole("button", { name: /Свернуть/ })).toBeInTheDocument();
    expect(within(card("GFC-Russia")).getByRole("button", { name: /Подробнее/ })).toBeInTheDocument();
    again.unmount();
    setup(2); // another search has its own state
    expect(within(card("SuomiFish")).getByRole("button", { name: /Подробнее/ })).toBeInTheDocument();
  });

  it("expands and collapses everything", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Развернуть все" }));
    expect(screen.getAllByRole("button", { name: /Свернуть ▴/ })).toHaveLength(6);
    await user.click(screen.getByRole("button", { name: "Свернуть все" }));
    expect(screen.getAllByRole("button", { name: /Подробнее/ })).toHaveLength(6);
  });
});

describe("sources toggle", () => {
  it("hides source links by default and shows them inline when switched on", async () => {
    const { user } = setup();
    await user.click(within(card("SuomiFish")).getByRole("button", { name: /Подробнее/ }));
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
    expect(order()[0]).toBe("SuomiFish");
    await user.click(screen.getByRole("button", { name: /^Оценка/ }));
    expect(order()[0]).toBe("SuomiFish"); // best score first
    await user.click(screen.getByRole("button", { name: /^Оценка/ }));
    expect(order()[5]).toBe("SuomiFish");
    await user.click(screen.getByRole("button", { name: /^Цена на товар/ }));
    expect(order()[0]).toBe("SuomiFish"); // the only supplier with a price: others (no price) stay last
    await user.click(screen.getByRole("button", { name: /^Цена на товар/ }));
    expect(order()[0]).toBe("SuomiFish"); // no price is always last, whatever the direction
    await user.click(screen.getByRole("button", { name: /^Мин\. заказ/ }));
    expect(order()[0]).toBe("Сушимаг"); // 2 000 ₽ is the smallest minimum order
    await user.click(screen.getByRole("button", { name: /^Мин\. заказ/ }));
    expect(order()[0]).toBe("GFC-Russia"); // 11 000 ₽
  });

  it("shows headline price per unit, minimum order and warning counts", async () => {
    await table();
    const row = screen.getByRole("row", { name: /SuomiFish/ });
    expect(within(row).getByText(/≈84 ₽\/кг/)).toBeInTheDocument();
    expect(within(screen.getByRole("row", { name: /Восторг/ })).getByTitle(/Домен почты/)).toBeInTheDocument();
  });
});

describe("comparison (regression: selecting suppliers and pressing «Сравнить» opens the comparison)", () => {
  const pick = async (user: ReturnType<typeof userEvent.setup>, names: string[]) => {
    for (const name of names) await user.click(screen.getByRole("checkbox", { name: `Сравнить: ${name}` }));
  };

  it.each([
    [2, ["SuomiFish", "GFC-Russia"]],
    [3, ["SuomiFish", "GFC-Russia", "Восторг"]],
    [4, ["SuomiFish", "GFC-Russia", "Восторг", "Сушимаг"]],
  ])("opens for %i selected suppliers and keeps the selection", async (count, names) => {
    const { user } = setup();
    await pick(user, names);
    expect(screen.getByText(new RegExp(`Для сравнения выбрано: ${count} из 4`))).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Сравнить" }));
    expect(screen.getByRole("heading", { name: `Сравнение (${count})` })).toBeInTheDocument();
    for (const name of names) expect(screen.getByRole("checkbox", { name: `Сравнить: ${name}` })).toBeChecked();
  });

  it("needs two suppliers, limits the selection to four and can be reset", async () => {
    const { user } = setup();
    await pick(user, ["SuomiFish"]);
    expect(screen.getByRole("button", { name: "Сравнить" })).toBeDisabled();
    await pick(user, ["GFC-Russia", "Восторг", "Сушимаг"]);
    expect(screen.getByRole("checkbox", { name: "Сравнить: ТД Фиш Сити" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Сбросить" }));
    expect(screen.queryByText(/Для сравнения выбрано/)).not.toBeInTheDocument();
  });

  it("works from the table view as well and a supplier can be removed from the comparison", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Таблица" }));
    await pick(user, ["SuomiFish", "GFC-Russia"]);
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

describe("statuses, filter, freshness and export", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("saves a status, shows it, filters by it and exports in the shown order", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ value: "called", updated_at: "2026-10-26T10:00:00+00:00" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { user } = setup();
    const suomiId = suppliers.find((s) => s.name === "SuomiFish")!.id;
    await user.selectOptions(screen.getByRole("combobox", { name: "Статус: SuomiFish" }), "called");
    expect(fetchMock).toHaveBeenCalledWith(`/api/runs/1/suppliers/${suomiId}/status`, expect.objectContaining({ method: "PUT", body: JSON.stringify({ status: "called" }) }));
    expect(screen.getByRole("combobox", { name: "Статус: SuomiFish" })).toHaveValue("called");

    await user.selectOptions(screen.getByRole("combobox", { name: "Фильтр по статусу" }), "called");
    expect(screen.getAllByRole("article")).toHaveLength(1);
    const link = screen.getByRole("link", { name: /Скачать в Excel/ });
    expect(link).toHaveAccessibleName("Скачать в Excel (показанные: 1)");
    expect(exported(link)).toEqual({ format: "xlsx", ids: String(suomiId) });
  });

  it("puts a failed status save back and tells the user", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 500 })));
    const { user } = setup();
    await user.selectOptions(screen.getByRole("combobox", { name: "Статус: SuomiFish" }), "declined");
    expect(await screen.findByText(/Не удалось сохранить статус/)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Статус: SuomiFish" })).toHaveValue("new");
  });

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
    const sorted = [...suppliers].sort((a, b) => (a.name === "Сушимаг" ? -1 : b.name === "Сушимаг" ? 1 : 0));
    expect(exported(screen.getByRole("link", { name: /Скачать в Excel/ })).ids.split(",")[0]).toBe(String(sorted[0].id));
  });

  it("shows the date the data was collected on the card", () => {
    setup();
    const dated = suppliers.filter((s) => s.collected_at);
    expect(dated.length).toBeGreaterThan(0);
    expect(within(card(dated[0].name)).getByText(/Данные собраны: \d{2}\.\d{2}\.\d{4}/)).toBeInTheDocument();
  });
});
