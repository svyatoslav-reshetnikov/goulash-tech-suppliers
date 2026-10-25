# Changelog

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
