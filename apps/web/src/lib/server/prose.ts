import { sql } from "drizzle-orm";
import * as v from "valibot";
import { origins } from "#lib/contrib.js";
import { getLocale } from "#lib/paraglide/runtime.js";

export const proseEntries = {
  body: v.string(),
  lang: v.string(),
  source_lang: v.nullable(v.string()),
  origin: v.nullable(v.picklist(origins)),
  stale: v.boolean(),
  alternate: v.nullable(v.string()),
};

export const proseColumns = sql.raw(
  "prose.body, prose.lang, prose.source_lang, prose.origin, prose.stale, prose.alternate",
);

export const proseJoin = (note: "n") => {
  const locale = getLocale();
  const base = locale.split("-", 1)[0] ?? locale;
  const alias = sql.raw(note);
  return sql`
    left join lateral (
      select coalesce(t.body, ${alias}.body_md) as body,
             coalesce(t.lang, ${alias}.lang) as lang,
             case when t.body is not null then ${alias}.lang end as source_lang,
             t.origin::text as origin,
             coalesce(t.stale_from is not null, false) as stale,
             case
               when t.body is not null then ${alias}.body_md
               when ${alias}.lang not in (${locale}, ${base}, 'en') then (
                 select e.body from translation e where e.source_md5 = md5(${alias}.body_md) and e.lang = 'en'
               )
             end as alternate
      from (select) one
      left join lateral (
        select body, lang, origin, stale_from from translation
        where source_md5 = md5(${alias}.body_md) and lang in (${locale}, ${base})
          and ${alias}.lang not in (${locale}, ${base})
        order by lang = ${locale} desc
        limit 1
      ) t on true
    ) prose on true
  `;
};
