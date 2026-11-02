export type Entry = {
  id: string;
  title: string;
  updated: string;
  author: string | null;
  path: string | null;
};

const escape = (value: string) =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

const instant = (value: string) => new Date(value).toISOString();

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u;

const identify = (origin: string, id: string) =>
  UUID.test(id)
    ? `urn:uuid:${id}`
    : `tag:${new URL(origin).hostname},2026:${id}`;

export function atom({
  origin,
  self,
  page,
  title,
  subtitle,
  entries,
  fallback,
}: {
  origin: string;
  self: string;
  page: string;
  title: string;
  subtitle: string;
  entries: Array<Entry>;
  fallback: string;
}) {
  const updated = entries[0]?.updated ?? fallback;
  const body = [
    '<?xml version="1.0" encoding="utf-8"?>',
    '<feed xmlns="http://www.w3.org/2005/Atom">',
    `<id>${escape(origin + self)}</id>`,
    `<title>${escape(title)}</title>`,
    `<subtitle>${escape(subtitle)}</subtitle>`,
    `<updated>${instant(updated)}</updated>`,
    `<link rel="self" type="application/atom+xml" href="${escape(origin + self)}"/>`,
    `<link rel="alternate" type="text/html" href="${escape(origin + page)}"/>`,
    "<author><name>electionsarchive.com</name></author>",
    ...entries.map((entry) =>
      [
        "<entry>",
        `<id>${escape(identify(origin, entry.id))}</id>`,
        `<title>${escape(entry.title)}</title>`,
        `<updated>${instant(entry.updated)}</updated>`,
        entry.path == null
          ? `<summary>${escape(entry.title)}</summary>`
          : `<link rel="alternate" type="text/html" href="${escape(origin + entry.path)}"/>`,
        entry.author == null
          ? ""
          : `<author><name>${escape(entry.author)}</name></author>`,
        "</entry>",
      ].join(""),
    ),
    "</feed>",
  ].join("\n");
  return new Response(body, {
    headers: {
      "content-type": "application/atom+xml; charset=utf-8",
      "cache-control": "public, max-age=300",
    },
  });
}
