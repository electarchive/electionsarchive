import { getLocale } from "#lib/paraglide/runtime.js";

export function count(value: number) {
  return new Intl.NumberFormat(getLocale()).format(value);
}

export function precision(margin: number | null | undefined) {
  const gap = Math.abs(margin ?? 1) * 100;
  return gap >= 1 || gap === 0
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
