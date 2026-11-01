import type { LiveOption } from "@ea/analytics/live";
import { dates, numbers } from "#lib/format.js";
import { prefs } from "#lib/live/prefs.svelte.js";
import { swatch } from "#lib/party.js";

export function tone(option: LiveOption | undefined) {
  if (option == null) {
    return "var(--muted-foreground)";
  }
  return swatch(
    option.party?.color === "" ? null : option.party?.color,
    option.party == null
      ? `candidate:${option.label.toLowerCase()}`
      : `party:${option.party.id}`,
  );
}

const SUFFIXES = new Set(["jr", "sr", "ii", "iii", "iv", "v"]);

export function surname(option: LiveOption | undefined) {
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
