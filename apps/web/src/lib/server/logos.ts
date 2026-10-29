import { sql } from "drizzle-orm";
import type { Logo } from "#lib/logo.js";
import { db } from "#lib/server/db/index.js";

const PARTY =
  /^(?:party:)?([\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12})$/iu;

export function partiesIn(value: unknown, found = new Set<string>()) {
  if (value == null || typeof value !== "object") {
    return found;
  }
  for (const [key, item] of Object.entries(value)) {
    const party =
      typeof item === "string" &&
      (key === "party" || key === "partyId" || item.startsWith("party:"))
        ? PARTY.exec(item)?.[1]
        : null;
    if (party == null) {
      partiesIn(item, found);
    } else {
      found.add(party.toLowerCase());
    }
  }
  return found;
}

export async function logos(
  parties: Iterable<string>,
  date: string | null,
  isStrict = false,
) {
  const ids = [...new Set(parties)];
  if (ids.length === 0) {
    return {};
  }
  const found = await db.execute<{
    party: string;
    path: string;
    width: number;
    height: number;
    mark_path: string;
    shape: "mark" | "wordmark";
    is_plate: boolean;
    is_light: boolean;
    license: string;
    artist: string | null;
    source: string;
  }>(sql`
    with img as (
      select i.*, case i.valid_from_precision
          when 'year' then (i.valid_from + interval '1 year')::date
          when 'month' then (i.valid_from + interval '1 month')::date
          else i.valid_from end as f
      from party_image i where i.party_id = any(${sql.param(ids)}::uuid[])
    ), named as (
      select entity_id, max(coalesce(upper(valid), 'infinity'::date)) as last, min(lower(valid)) as founded
      from name where kind in ('official', 'abbreviation') and entity_id = any(${sql.param(ids)}::uuid[])
      group by 1
    ), day as (
      select coalesce(${date}::date, current_date) as d
    ), fit as (
      select img.*, case
          when img.dated_by <> 'inception' then (img.f is null or img.f <= day.d) and (img.valid_to is null or day.d < img.valid_to)
          else not ${isStrict}
            and day.d >= coalesce((select max(coalesce(x.valid_to, 'infinity'::date)) from img x
                                   where x.party_id = img.party_id and x.dated_by <> 'inception'), '-infinity'::date)
            and (img.f is null or img.f <= day.d)
            and (img.f is not null or not exists (select from named l where l.entity_id = img.party_id and l.founded > day.d))
            and not exists (select from name n join named l on l.entity_id = n.entity_id
                            where n.entity_id = img.party_id and n.kind in ('official', 'abbreviation')
                              and upper(n.valid) < l.last and n.valid @> day.d)
        end as fits,
        ${date}::date is null and img.dated_by <> 'inception'
          and exists (select from named l where l.entity_id = img.party_id and l.last < current_date) as last
      from img, day
    )
    select distinct on (party_id) party_id::text as party, path, width, height, mark_path, shape, is_plate,
      is_light, license, artist,
      source_url as source
    from fit where fits or last
    order by party_id, fits desc, (dated_by is null or dated_by = 'inception'), valid_to desc nulls first, f desc nulls last,
      abs(ln(width::float / height)), path
  `);
  return Object.fromEntries(
    found.rows.map((row): [string, Logo] => [
      row.party,
      {
        src: `/${row.path}`,
        width: row.width,
        height: row.height,
        mark: `/${row.mark_path}`,
        shape: row.shape,
        isPlate: row.is_plate,
        isLight: row.is_light,
        license: row.license,
        artist: row.artist,
        source: row.source,
      },
    ]),
  );
}

export async function withLogos<T extends object>(
  value: T,
  date: string | null,
) {
  return { ...value, logos: await logos(partiesIn(value), date) };
}
