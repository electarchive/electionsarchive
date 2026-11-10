import type { Forms } from "#lib/noun-forms.js";
import { dictionary } from "#lib/dictionary.js";
import { FORMS } from "#lib/noun-forms.js";

const ENGLISH = [
  "2024 constituency",
  "aggregate",
  "assembly",
  "Assembly constituency",
  "assembly district",
  "ballot box",
  "borough",
  "canton",
  "caucus precinct",
  "census tract",
  "combined authority",
  "commune",
  "congressional district",
  "constituency",
  "contest",
  "council area",
  "counting area",
  "counting district",
  "country",
  "country abroad",
  "county",
  "county part",
  "Dáil constituency",
  "department",
  "district",
  "district electoral area",
  "district electoral area (1973 to 1981)",
  "district electoral area (1985 to 1989)",
  "district electoral area (1993 to 2014)",
  "division abroad",
  "election district",
  "electoral district",
  "electoral division",
  "electoral region",
  "European Parliament constituency",
  "European Parliament electoral region",
  "former county",
  "house district",
  "judicial district",
  "local government district (1973 to 2015)",
  "member state",
  "metropolitan municipality",
  "multi-member college",
  "municipality",
  "municipality part",
  "nation",
  "neighborhood",
  "place",
  "police area",
  "polling district",
  "polling division",
  "polling place",
  "polling section",
  "polling station",
  "precinct",
  "prison",
  "province",
  "province or territory",
  "region",
  "return unit",
  "school district",
  "section",
  "sector",
  "Senate college",
  "Senate district",
  "Senate multi-member college",
  "Senate region",
  "Senate single-member college",
  "single-member college",
  "special district",
  "state",
  "state house district",
  "state senate district",
  "town",
  "voting district",
  "ward",
] as const;

const TABLES = new Map<string, Map<string, Forms>>();

function table(locale: string) {
  const known = TABLES.get(locale);
  if (known != null) {
    return known;
  }
  const source = import.meta.env.SSR ? FORMS : dictionary.nouns;
  const forms = source?.[locale] ?? [];
  const built = new Map<string, Forms>();
  for (const [index, english] of ENGLISH.entries()) {
    const entry = forms[index];
    if (entry != null) {
      built.set(english, entry);
      built.set(entry[0], entry);
    }
  }
  if (source != null) {
    TABLES.set(locale, built);
  }
  return built;
}

export function localNoun(noun: string, locale: string) {
  return table(locale).get(noun)?.[0];
}

export function localNouns(noun: string, locale: string) {
  return table(locale).get(noun)?.[1];
}
