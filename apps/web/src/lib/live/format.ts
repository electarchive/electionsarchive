import type { LiveOption } from "@ea/analytics/live";
import { dates, numbers } from "#lib/format.js";
import { prefs } from "#lib/live/prefs.svelte.js";
import { side, stance, swatch } from "#lib/party.js";

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

export type Band = { ids: Array<string>; isProjected: boolean };

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

export function lead(value: number, digits = 1) {
  return numbers({
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Math.abs(value) * 100);
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
  return dates({ hour: "numeric", minute: "2-digit", timeZone: zone }).format(
    new Date(at),
  );
}

export function stamp(at: string | number | null, zone = prefs.zone) {
  if (at == null) {
    return "";
  }
  return dates({
    hour: "numeric",
    minute: "2-digit",
    timeZone: zone,
    timeZoneName: "short",
  }).format(new Date(at));
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

export const HATCH =
  "repeating-linear-gradient(135deg, color-mix(in oklab, var(--foreground) 32%, transparent) 0 1px, transparent 1px 4px)";
