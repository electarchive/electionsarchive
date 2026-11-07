import type { LiveOption } from "@ea/analytics/live";
import {
  calendarDay,
  dateParts,
  dates,
  numbers,
  precision,
  timeOrDate,
  today,
} from "#lib/format.js";
import { prefs } from "#lib/live/prefs.svelte.js";
import { isNeutral, side, stance, swatch } from "#lib/party.js";

export function tone(option: LiveOption | undefined) {
  if (option == null) {
    return "var(--muted-foreground)";
  }
  const answer = `answer:${option.label.toLowerCase()}`;
  if (option.party == null && side(answer) != null) {
    return swatch(null, answer);
  }
  return swatch(
    option.party?.color === "" ? null : (option.party?.color ?? option.color),
    option.party == null
      ? `candidate:${option.label.toLowerCase()}`
      : `party:${option.party.id}`,
  );
}

export type Band = {
  ids: Array<string>;
  isProjected: boolean;
  isUnopposed: boolean;
  fill: string;
};

export function bandFor(
  options: Array<LiveOption>,
  ids: Array<string>,
  seats: number,
  isProjected: boolean,
  isUnopposed = false,
): Band | null {
  if (ids.length === 0) {
    return null;
  }
  const fill =
    seats > 1 || ids.length > 1
      ? null
      : tone(options.find((option) => option.id === ids[0]));
  return {
    ids,
    isProjected,
    isUnopposed,
    fill: fill == null || isNeutral(fill) ? "var(--marker)" : fill,
  };
}

export function paint(option: LiveOption | undefined, band: Band | null) {
  if (band == null) {
    return tone(option);
  }
  return option != null && band.ids.includes(option.id)
    ? "var(--foreground)"
    : "color-mix(in oklab, var(--foreground) 34%, transparent)";
}

const SUFFIXES = new Set(["jr", "sr", "ii", "iii", "iv", "v"]);

export function surname(option: LiveOption | undefined) {
  if (option?.isAnswer === true) {
    return stance(option.label) ?? option.label;
  }
  const words = (option?.label ?? "")
    .trim()
    .split(/\s+/u)
    .map((word) => word.replace(/,$/u, ""));
  while (
    words.length > 1 &&
    SUFFIXES.has((words.at(-1) ?? "").replace(/\.$/u, "").toLowerCase())
  ) {
    words.pop();
  }
  return words.at(-1) ?? "";
}

export function initial(option: LiveOption | undefined) {
  const party = option?.party?.short ?? option?.party?.name ?? "";
  return (party === "" ? surname(option) : party).slice(0, 1).toUpperCase();
}

export function lead(value: number, digits = precision(value)) {
  return numbers({
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Math.abs(value) * 100);
}

export function places(gaps: Array<number>) {
  let most = 1;
  for (const gap of gaps) {
    most = Math.max(most, precision(gap));
  }
  return most;
}

export function placesOf(votes: Array<number>) {
  let first = 0;
  let second = 0;
  let total = 0;
  for (const value of votes) {
    total += value;
    if (value > first) {
      second = first;
      first = value;
    } else if (value > second) {
      second = value;
    }
  }
  return total > 0 ? precision((first - second) / total) : 1;
}

export function across(low: number, high: number, least = 0) {
  const width = Math.abs(high - low) * 100;
  return width === 0
    ? least
    : Math.max(least, Math.min(4, Math.ceil(-Math.log10(width))));
}

export function ends(low: number, high: number) {
  const least = across(low, high, 1);
  return {
    low: lead(low, Math.max(least, precision(low))),
    high: lead(high, Math.max(least, precision(high))),
  };
}

export function apart(first: number, second: number, digits: number) {
  const scale = 10 ** (digits + 2);
  return (Math.round(first * scale) - Math.round(second * scale)) / scale;
}

export function compact(value: number) {
  return numbers({
    notation: "compact",
    maximumFractionDigits: value >= 1e6 ? 2 : 1,
  }).format(value);
}

export function clock(at: string | number | null, zone = prefs.zone) {
  if (at == null) {
    return "";
  }
  return timeOrDate(new Date(at), { timeZone: zone });
}

export function stamp(at: string | number | null, zone = prefs.zone) {
  if (at == null) {
    return "";
  }
  return timeOrDate(new Date(at), { timeZone: zone, timeZoneName: "short" });
}

export function dated(at: string | number | null, zone = prefs.zone) {
  if (at == null) {
    return "";
  }
  return dates({
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: zone,
    timeZoneName: "short",
  }).format(new Date(at));
}

const HOUR = 3_600_000;
const MARK_HOURS = [1, 2, 3, 4, 6, 12, 24];

export function timeMarks(
  start: number,
  end: number,
  plot: number,
  zone = prefs.zone,
) {
  const first = calendarDay(start, zone);
  const isOneDay = first === calendarDay(end, zone);
  const isAllToday = isOneDay && first === today(zone);
  const room = isOneDay ? 72 : 96;
  const perHour = (Math.max(1, plot) / Math.max(1, end - start)) * HOUR;
  const step =
    MARK_HOURS.find((hours) => hours * perHour >= room) ??
    24 * Math.ceil(room / (24 * perHour));
  const hourOf = dates({
    hour: "numeric",
    hourCycle: "h23",
    numberingSystem: "latn",
    timeZone: zone,
  });
  const marks: Array<{ at: number; label: string }> = [];
  let day: string | null = null;
  let midnights = 0;
  for (let at = Math.ceil(start / HOUR) * HOUR; at <= end; at += HOUR) {
    const hour = Number(
      hourOf.formatToParts(at).find((part) => part.type === "hour")?.value,
    );
    if (hour % Math.min(step, 24) !== 0) {
      continue;
    }
    if (step > 24 && midnights++ % (step / 24) !== 0) {
      continue;
    }
    const shown = calendarDay(at, zone);
    const isNewDay = shown !== day;
    day = shown;
    marks.push({
      at,
      label: dates({
        ...(step < 24 && { hour: "numeric", minute: "2-digit" }),
        ...((step >= 24 || (isNewDay && !isAllToday)) && dateParts(at, zone)),
        timeZone: zone,
      }).format(at),
    });
  }
  return marks;
}

export function day(at: string | number, zone = prefs.zone) {
  return dates({
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: zone,
  }).format(new Date(at));
}

export function zoneName(at: string | number, zone = prefs.zone) {
  return (
    dates({ timeZoneName: "short", timeZone: zone })
      .formatToParts(new Date(at))
      .find((part) => part.type === "timeZoneName")?.value ?? zone
  );
}

export function isDistricted(state: string) {
  return state === "ak";
}

export function placeScheme(state: string) {
  return isDistricted(state) ? "us.sldl" : "us.county";
}

export function placeName(name: string) {
  return name.replace(/, [A-Z]{2}$/u, "");
}

export function placeSlug(name: string) {
  return placeName(name)
    .normalize("NFKD")
    .replaceAll(/\p{M}/gu, "")
    .toLowerCase()
    .replaceAll(/[^a-z0-9]+/gu, "-")
    .replaceAll(/^-|-$/gu, "");
}

export const DOTS =
  "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='4' height='4'%3E%3Ccircle cx='2' cy='2' r='0.85' fill='%23808080' fill-opacity='0.7'/%3E%3C/svg%3E\")";
