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
  const format = new Intl.NumberFormat(getLocale(), {
    style: "percent",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
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
