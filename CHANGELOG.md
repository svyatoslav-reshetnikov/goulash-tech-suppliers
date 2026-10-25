# Changelog

## Phase 3: export and work tracking

- `GET /api/runs/{id}/export?format=xlsx|csv&ids=...`: the whole search or the chosen suppliers in the given (table) order. `.xlsx` is
  written by a small built-in writer (no new dependency); CSV is the fallback (UTF-8 with BOM, `;` delimiter, cells that Excel could
  read as formulas are prefixed with an apostrophe). Columns = table columns + all phones/emails, address, site, INN, note,
  source URLs, warnings, collection date, search parameters and search date. File name `suppliers_<product>_<region>_<date>.xlsx`.
  The endpoint sits behind the existing session auth.
- Work statuses per supplier within a search: `new`, `called`, `awaiting_price`, `shortlisted`, `declined`
  (`PUT /api/runs/{id}/suppliers/{supplier}/status`, table `supplier_statuses`, migration 0008, idempotent). Select on the card and in
  the table, filter above the list, included in the export. Notes stay as they were.
- "Данные собраны: dd.mm.yyyy" per supplier: the crawl time of the supplier's site, falling back to the search finish date.
- The UI export button follows the screen: chosen suppliers, otherwise the shown list, in table order.

## Phase 2: results layout

- Supplier cards are collapsed by default: name, type, score, one region badge, headline price, minimum order, main phone and
  email (tap-to-call / mailto), warning chips and one "Уточнить" line. "Подробнее" expands the full card (`aria-expanded`).
- One page-level toggle "Показывать источники" (off by default) replaces the per-value source buttons; on, it shows source links inline.
- "Развернуть все / Свернуть все"; expanded state, view mode and the source toggle are remembered per search (sessionStorage)
  while switching tabs.
- "Карточки / Таблица" toggle. Table columns: rank, supplier, type, score, region, price per base unit, minimum order, delivery,
  phone, email, warnings; every column sorts (missing values always last); horizontal scroll on narrow screens.
- Compare works from cards and from the table; a supplier can be removed from the comparison. Regression tests cover 2, 3 and 4
  selected suppliers, the minimum of two, the limit of four and reset.
- Main container widened to 1100 px; scraped URLs are rendered as links only when they are http(s).
- Frontend tests (Vitest + Testing Library, dev dependencies only) run in CI; `npm test`.
- Identical minimum-order values stored from two pages are shown once.

## Phase 1: result correctness (ranking, prices, warnings)

- Scoring weights are now product match 40, location 20, completeness 25, reliability 15 (constants in `backend/app/ranking/config.py`).
  The supplier type no longer adds points; a mismatching type only blocks the recommendation badge.
- Product match rests on evidence: price list 40, assortment 32, description 20, company name only 10, generic claim ("полный ассортимент") 8.
  A phrase matches only when all its words are present; "рисовый уксус" is not rice. The breakdown shows what matched.
- "Рекомендуем связаться" needs a confirmed product (at least half of the product points), a known region, a phone and a fitting type.
- Prices and minimum orders are parsed deterministically from the stored raw strings into structured values
  (item, pack, price, price per kg/l/piece); ambiguous strings are flagged "Уточните цену". One minimum-order line with units.
  Headline "Цена на искомый товар" shows only prices of the searched product; the rest go to "Прочие цены".
- Structured deterministic warnings (`email_domain_mismatch`, `low_evidence`, `region_unknown`, `product_unconfirmed`, `no_inn`).
- Extraction prompt asks for "товар, фасовка — цена" and units in the minimum order.
- Old results are re-scored on read from the stored fields; no migration was needed.
