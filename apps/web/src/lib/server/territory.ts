import aliases from "cldr-core/supplemental/aliases.json" with { type: "json" };
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { remember } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";

const replacements = new Map<string, string>();
for (const [code, { _replacement }] of Object.entries(
  aliases.supplemental.metadata.alias.territoryAlias,
)) {
  const targets = _replacement.split(" ");
  if (targets.length === 1 && targets[0] != null) {
    replacements.set(code, targets[0].toLowerCase());
  }
}

async function roots() {
  return remember("territory_roots", async () =>
    (
      await db.execute<{ path: string }>(sql`
        select s.path from slug s join polity p on p.id = s.entity_id
        where s.is_current and strpos(s.path, '/') = 0
      `)
    ).rows.map((row) => row.path),
  );
}

export async function canonicalPath(path: string) {
  const [head = "", ...rest] = path.split("/");
  const upper = head.toUpperCase();
  const canonical = replacements.get(upper) ?? upper.toLowerCase();
  if (canonical === head) {
    return path;
  }
  const known = new Set(await roots());
  return known.has(head) || !known.has(canonical)
    ? path
    : [canonical, ...rest].join("/");
}

function aliased(description: string) {
  return v.pipeAsync(
    v.string(),
    v.transformAsync(canonicalPath),
    v.description(
      `${description}; CLDR territory aliases in any case (uk, GBR, 826) are read as the polity they name`,
    ),
  );
}

export const Polity = aliased("A polity's path, such as gb");

export const Path = aliased("A site path, such as gb/2024-07-04");
