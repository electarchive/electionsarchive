import { getLocale } from "#lib/paraglide/runtime.js";

export function count(value: number) {
  return new Intl.NumberFormat(getLocale()).format(value);
}

export function percent(value: number) {
  return new Intl.NumberFormat(getLocale(), {
    style: "percent",
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(value);
}

export function points(value: number) {
  return new Intl.NumberFormat(getLocale(), {
    signDisplay: "exceptZero",
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(value * 100);
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
