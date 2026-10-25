import type { Commerce, MinOrder, Price } from "../api";

/** One price line: the server-built display string, plus a note when the number needs confirmation. */
export function PriceLine({ price }: { price: Price }) {
  return (
    <span className={price.needs_confirmation ? "text-amber-900" : undefined}>
      {price.display}
      {price.note && <span className="ml-1 text-xs text-amber-700">· {price.note}</span>}
    </span>
  );
}

export function MinOrderLine({ order }: { order: MinOrder }) {
  return <span>{order.display}</span>;
}

/** Headline price for the searched product; other prices are collapsed. Falls back to nothing when the server sent no structure. */
export function PriceBlock({ commerce }: { commerce: Commerce }) {
  const { price_headline: headline, prices_other: others } = commerce;
  return (
    <div className="space-y-1 text-sm">
      {headline ? (
        <div>
          <PriceLine price={headline} />
        </div>
      ) : (
        <div className="text-slate-500">Цена на искомый товар не найдена</div>
      )}
      {others.length > 0 && (
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer">Прочие цены ({others.length})</summary>
          <ul className="mt-1 space-y-0.5">
            {others.map((p) => (
              <li key={p.raw + p.display}>
                <PriceLine price={p} />
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
