import { m } from "#lib/paraglide/messages.js";
import { getLocale } from "#lib/paraglide/runtime.js";

export function count(value: number) {
  return new Intl.NumberFormat(getLocale()).format(value);
}

export function precision(margin: number | null | undefined) {
  const gap = Math.abs(margin ?? 1) * 100;
  return gap === 0 || gap >= 1
    ? 1
    : Math.min(6, Math.max(2, Math.floor(-Math.log10(gap)) + 1));
}

export function percent(value: number, digits = 1) {
  let shown = digits;
  while (
    shown < 4 &&
    [0.5, 1].some(
      (line) =>
        value < line &&
        Math.round(value * 10 ** (shown + 2)) >= line * 10 ** (shown + 2),
    )
  ) {
    shown += 1;
  }
  const format = new Intl.NumberFormat(getLocale(), {
    style: "percent",
    minimumFractionDigits: shown,
    maximumFractionDigits: shown,
  });
  const floor = 10 ** -(digits + 2);
  return value > 0 && value < floor / 2
    ? `<${format.format(floor)}`
    : format.format(value);
}

export function points(value: number, digits = precision(value)) {
  return new Intl.NumberFormat(getLocale(), {
    signDisplay: "exceptZero",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
    .format(value * 100)
    .replace("-", "−");
}

export function date(value: string | null) {
  if (value == null) {
    return "";
  }
  if (/^\d{4}$/u.test(value)) {
    return value;
  }
  if (/^\d{4}-\d{2}$/u.test(value)) {
    return new Intl.DateTimeFormat(getLocale(), {
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    }).format(new Date(`${value}-01T00:00:00Z`));
  }
  return new Intl.DateTimeFormat(getLocale(), {
    dateStyle: "long",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

export function credit(photo: {
  license: string;
  artist: string | null;
  taken: { from: number | null; to: number | null } | null;
}) {
  const from = photo.taken?.from;
  const to = photo.taken?.to;
  const when =
    from == null
      ? to == null
        ? null
        : m.photo_taken_before({ year: String(to) })
      : to == null
        ? m.photo_taken_after({ year: String(from) })
        : from === to
          ? String(from)
          : m.photo_taken_range({ first: String(from), last: String(to) });
  if (when == null) {
    return photo.artist == null
      ? m.photo_credit_anonymous({ license: photo.license })
      : m.photo_credit({ artist: photo.artist, license: photo.license });
  }
  return photo.artist == null
    ? m.photo_credit_anonymous_dated({ date: when, license: photo.license })
    : m.photo_credit_dated({
        artist: photo.artist,
        date: when,
        license: photo.license,
      });
}

export function definite(label: string, isStart = false) {
  return !/^the /iu.test(label) &&
    /\b(?:Party|Front|League|Alliance|Union|Coalition|Movement|Federation|Bloc|Congress|Rally|States|Kingdom|Republic|Netherlands|Islands)(?: (?:of|for) .+)?$/u.test(
      label,
    )
    ? `${isStart ? "The" : "the"} ${label}`
    : label;
}

export function plural(noun: string) {
  const fixed: Record<string, string> = {
    "country abroad": "countries abroad",
    "province or territory": "provinces or territories",
  };
  return (
    fixed[noun] ??
    (/[^aeiou]y$/u.test(noun)
      ? `${noun.slice(0, -1)}ies`
      : /(?:s|x|ch|sh)$/u.test(noun)
        ? `${noun}es`
        : `${noun}s`)
  );
}

export function initials(label: string) {
  return label
    .split(/\s+/u)
    .filter((word) => !/^(?:jr|sr|ii|iii|iv)\.?,?$/iu.test(word))
    .filter((word, index, words) => [0, words.length - 1].includes(index))
    .map((word) => word.slice(0, 1))
    .join("");
}
