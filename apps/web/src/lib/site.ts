import { dates } from "#lib/format.js";
import { m } from "#lib/paraglide/messages.js";

export const REPOSITORY = "https://github.com/electarchive/electionsarchive";

export const FORMATS = ["APA", "Chicago", "BibTeX", "CSL-JSON"] as const;

export type Format = (typeof FORMATS)[number];

export function moment(value: string) {
  return dates({
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(new Date(value));
}

export function cite(
  format: Format,
  {
    origin,
    published,
    release,
    today,
  }: {
    origin: string;
    published: string | null;
    release: { calver: string; doi: string | null } | null;
    today: Date;
  },
) {
  const title = m.wordmark();
  const issued = new Date(published ?? today);
  const parts = (value: Date) => [
    value.getUTCFullYear(),
    value.getUTCMonth() + 1,
    value.getUTCDate(),
  ];
  const stamp =
    release == null
      ? published == null
        ? null
        : `Data as of ${published.slice(0, 16).replace("T", " ")} UTC`
      : `Version ${release.calver}`;
  const link = release?.doi == null ? origin : `https://doi.org/${release.doi}`;
  const accessed = today.toISOString().slice(0, 10);
  const retrieved = new Intl.DateTimeFormat("en-US", {
    dateStyle: "long",
    timeZone: "UTC",
  }).format(today);
  switch (format) {
    case "APA": {
      return `Ciftci, F. (${issued.getUTCFullYear()}). ${title}${stamp == null ? "" : ` (${stamp})`} [Data set]. ${release?.doi == null ? `Retrieved ${retrieved}, from ${link}` : link}`;
    }
    case "Chicago": {
      return `Ciftci, Firat. ${issued.getUTCFullYear()}. ${title}.${stamp == null ? "" : ` ${stamp}.`} Dataset.${release?.doi == null ? ` Accessed ${retrieved}.` : ""} ${link}.`;
    }
    case "BibTeX": {
      return [
        `@misc{electionsarchive${issued.getUTCFullYear()},`,
        "  author = {Ciftci, Firat},",
        `  title = {${title}},`,
        `  year = {${issued.getUTCFullYear()}},`,
        ...(release == null ? [] : [`  version = {${release.calver}},`]),
        ...(release?.doi == null ? [] : [`  doi = {${release.doi}},`]),
        `  url = {${link}},`,
        ...(stamp == null ? [] : [`  note = {${stamp}},`]),
        `  urldate = {${accessed}},`,
        "}",
      ].join("\n");
    }
    case "CSL-JSON": {
      return JSON.stringify(
        [
          {
            id: "electionsarchive",
            type: "dataset",
            title,
            author: [{ family: "Ciftci", given: "Firat" }],
            issued: { "date-parts": [parts(issued)] },
            ...(stamp != null && { version: release?.calver ?? stamp }),
            ...(release?.doi != null && { DOI: release.doi }),
            URL: link,
            accessed: { "date-parts": [parts(today)] },
          },
        ],
        null,
        2,
      );
    }
  }
}

export function area(subject: string) {
  const found = /^([a-z0-9._/-]+): (.+)$/su.exec(subject);
  return found?.[1] == null || found[2] == null
    ? { area: null, text: subject }
    : { area: found[1], text: found[2] };
}
