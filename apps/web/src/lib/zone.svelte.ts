import { dates } from "#lib/format.js";

export const zone = $state({ name: "UTC" });

export function localizeZone() {
  zone.name = new Intl.DateTimeFormat().resolvedOptions().timeZone;
}

const STYLES = {
  time: { hour: "numeric", minute: "2-digit", timeZoneName: "short" },
  date: { dateStyle: "long" },
  full: {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  },
  utc: {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  },
} satisfies Record<string, Intl.DateTimeFormatOptions>;

export function instant(at: string) {
  return new Date(at.replace(" ", "T").replace(/([+-]\d{2})$/u, "$1:00"));
}

export type MomentStyle = Exclude<keyof typeof STYLES, "utc">;

export function moment(at: string, style: MomentStyle = "full") {
  return dates({ ...STYLES[style], timeZone: zone.name }).format(instant(at));
}

export function utc(at: string) {
  return dates({ ...STYLES.utc, timeZone: "UTC" }).format(instant(at));
}

export function localDay(at: string) {
  const parts = new Map(
    dates({
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      numberingSystem: "latn",
      timeZone: zone.name,
    })
      .formatToParts(instant(at))
      .map((part) => [part.type, part.value]),
  );
  return `${parts.get("year")}-${parts.get("month")}-${parts.get("day")}`;
}
