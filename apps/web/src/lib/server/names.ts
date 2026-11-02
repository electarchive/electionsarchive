import { sql } from "drizzle-orm";
import { createHash } from "node:crypto";
import { capitalize } from "#lib/format.js";
import { localNoun } from "#lib/nouns.js";
import { db } from "#lib/server/db/index.js";

const LONGEST = 300;
const NAMED = /[A-Z]/;
const NUMBERED = /^(\p{Lu}\D*?) (\S*\d\S*)$/u;

function isPlain(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value != null &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

function gather(value: unknown, found: Map<string, string>) {
  if (typeof value === "string") {
    if (value.length <= LONGEST && NAMED.test(value) && !found.has(value)) {
      found.set(value, createHash("md5").update(value).digest("hex"));
    }
  } else if (Array.isArray(value)) {
    for (const item of value) {
      gather(item, found);
    }
  } else if (isPlain(value)) {
    for (const item of Object.values(value)) {
      gather(item, found);
    }
  }
}

function numbered(value: string, locale: string) {
  const parts = NUMBERED.exec(value);
  if (parts == null) {
    return;
  }
  const head = parts[1] ?? "";
  const noun =
    localNoun(
      head.replace(/^./u, (first) => first.toLowerCase()),
      locale,
    ) ?? localNoun(head, locale);
  return noun == null ? undefined : `${capitalize(noun)} ${parts[2] ?? ""}`;
}

function swap(
  value: unknown,
  names: Map<string, string>,
  locale: string,
  key?: string,
): unknown {
  if (typeof value === "string") {
    return (
      names.get(value) ??
      (key === "noun" ? localNoun(value, locale) : numbered(value, locale)) ??
      value
    );
  }
  if (Array.isArray(value)) {
    return value.map((item) => swap(item, names, locale, key));
  }
  if (isPlain(value)) {
    const out: Record<string, unknown> = {};
    for (const [field, item] of Object.entries(value)) {
      out[field] = swap(item, names, locale, field);
    }
    return out;
  }
  return value;
}

export async function localizeNames<T>(value: T, locale: string): Promise<T> {
  const found = new Map<string, string>();
  gather(value, found);
  if (found.size === 0) {
    return swap(value, new Map(), locale) as T;
  }
  const texts = new Map<string, string>();
  for (const [text, md5] of found) {
    texts.set(md5, text);
  }
  const rows = await db.execute<{ source_md5: string; body: string }>(sql`
    select t.source_md5, t.body
    from translation t
    join changeset c on c.id = t.changeset_id and c.pipeline = ${`name_translations.${locale}`}
    where t.lang = ${locale} and t.source_md5 = any(${sql.param([...texts.keys()])}::text[])
  `);
  const names = new Map<string, string>();
  for (const row of rows.rows) {
    const text = texts.get(row.source_md5);
    if (text != null) {
      names.set(text, row.body);
    }
  }
  return swap(value, names, locale) as T;
}
