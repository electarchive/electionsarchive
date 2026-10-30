import { measureLabel } from "#lib/contrib.js";
import { OUTCOMES } from "#lib/outcomes.js";

export const outcomeKinds = [
  "elected",
  "won",
  "advanced",
  "tied",
  "lost",
  "withdrew",
  "disqualified",
  "declined",
] as const;

export const outcomeLabel = (kind: string) => OUTCOMES[kind]?.() ?? kind;

export const unitMeasures = [
  "registered",
  "ballots_cast",
  "valid",
  "invalid",
  "blank",
] as const;

export type Region = [number, number, number, number];

export type Mark = { page: number; region: Region };

export type Place = string | { scheme: string; code: string };

export type Sheet = {
  id: string;
  options: Array<{
    id: string;
    label: string;
    kind: string;
    position: number | null;
  }>;
  places: Array<{ unit: string; scheme: string; code: string; name: string }>;
  measures: Array<string>;
  set: { id: string; stage: string; source: string | null } | null;
};

export type Upload = { id: string; filename: string; pages: number | null };

export const measureKey = (measure: string) => `measure:${measure}`;

export const columnOf = (key: string) =>
  key.startsWith("measure:")
    ? { option: null, measure: key.slice(8) }
    : { option: key, measure: "votes" };

export const columnLabel = (key: string, sheet: Sheet | null) =>
  key.startsWith("measure:")
    ? measureLabel(key.slice(8))
    : (sheet?.options.find((option) => option.id === key)?.label ?? key);

export const figure = (raw: string) => {
  const text = raw.replaceAll(/[\s,']/gu, "");
  if (text === "") {
    return null;
  }
  return /^\d+(?:\.\d+)?$/u.test(text) ? Number(text) : NaN;
};

export const fold = (text: string) =>
  text
    .normalize("NFKD")
    .replaceAll(/\p{M}/gu, "")
    .toLowerCase()
    .replaceAll(/[^\p{L}\p{N}]+/gu, " ")
    .trim();

export const placeKey = (place: Place) =>
  typeof place === "string" ? place : `new:${place.scheme}:${place.code}`;

export const defaultColumns = (sheet: Sheet) => [
  ...sheet.options.map((option) => option.id),
  ...(sheet.measures.length === 0
    ? ["ballots_cast", "valid"]
    : unitMeasures.filter((measure) => sheet.measures.includes(measure))
  ).map((measure) => measureKey(measure)),
];

const splitLine = (line: string, separator: string) => {
  const cells: Array<string> = [];
  let cell = "";
  let isQuoted = false;
  for (let index = 0; index < line.length; index++) {
    const char = line[index];
    if (char === '"') {
      if (isQuoted && line[index + 1] === '"') {
        cell += '"';
        index++;
      } else {
        isQuoted = !isQuoted;
      }
    } else if (char === separator && !isQuoted) {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += char;
    }
  }
  cells.push(cell.trim());
  return cells;
};

export const splitTable = (text: string) => {
  const lines = text
    .replaceAll("\r", "")
    .split("\n")
    .filter((line) => line.trim() !== "");
  const separator = lines.some((line) => line.includes("\t"))
    ? "\t"
    : lines.some((line) => line.includes(";"))
      ? ";"
      : ",";
  return lines.map((line) => splitLine(line, separator));
};
