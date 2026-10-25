/** Rough cost and time of one search, from the measured runs on the demo stand (70-100 RUB, 2-4 minutes). */
export const COST_PER_SEARCH: [number, number] = [70, 100];
export const MINUTES_PER_SEARCH: [number, number] = [2, 4];

export function plural(n: number, one: string, few: string, many: string): string {
  const mod100 = n % 100;
  const mod10 = n % 10;
  if (mod100 >= 11 && mod100 <= 14) return many;
  return mod10 === 1 ? one : mod10 >= 2 && mod10 <= 4 ? few : many;
}

/** «Будет запущено 3 поиска, ≈ 210–300 ₽, 6–12 минут» */
export function estimateText(searches: number): string {
  const [c1, c2] = COST_PER_SEARCH.map((c) => c * searches);
  const [m1, m2] = MINUTES_PER_SEARCH.map((m) => m * searches);
  return `Будет запущено ${searches} ${plural(searches, "поиск", "поиска", "поисков")}, ≈ ${c1}–${c2} ₽, ${m1}–${m2} ${plural(m2, "минута", "минуты", "минут")}`;
}
