import { fold } from "#lib/live/local.js";

export type Hit = {
  slug: string;
  title: string;
  place: string;
  state: string;
  isMeasure: boolean;
  isLocal: boolean;
  match: string | null;
};

export function terms(text: string) {
  return fold(text)
    .replaceAll(/(?<=\p{L})(?=\p{N})|(?<=\p{N})(?=\p{L})/gu, " ")
    .split(/[^\p{L}\p{N}]+/u)
    .filter((term) => term !== "");
}

export function haystack(parts: Array<string>) {
  return ` ${[...new Set(parts.flatMap((part) => terms(part)))].join(" ")} `;
}

export function rate(hay: string, term: string) {
  if (hay.includes(` ${term} `)) {
    return 3;
  }
  return !/^\d+$/u.test(term) && hay.includes(` ${term}`) ? 2 : 0;
}

export function score(
  hay: string,
  people: Array<{ name: string; hay: string }>,
  wanted: Array<string>,
) {
  let total = 0;
  let match: string | null = null;
  for (const term of wanted) {
    const own = rate(hay, term);
    let best = own === 0 ? 0 : own + 1;
    if (best < 4) {
      for (const person of people) {
        const found = rate(person.hay, term);
        if (found > best) {
          best = found;
          match = person.name;
        }
      }
    }
    if (best === 0) {
      return null;
    }
    total += best;
  }
  return { total, match };
}

export function strongest<T extends { total: number }>(sorted: Array<T>) {
  const top = sorted[0]?.total ?? 0;
  return sorted.filter((entry) => entry.total >= top * 0.75);
}
