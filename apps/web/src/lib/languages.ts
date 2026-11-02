export const LANGUAGES: Record<string, string> = {
  bg: "Български",
  cs: "Čeština",
  cy: "Cymraeg",
  da: "Dansk",
  de: "Deutsch",
  el: "Ελληνικά",
  en: "English",
  es: "Español",
  et: "Eesti",
  fi: "Suomi",
  fr: "Français",
  ga: "Gaeilge",
  gd: "Gàidhlig",
  hr: "Hrvatski",
  hu: "Magyar",
  it: "Italiano",
  kmr: "Kurmancî",
  lt: "Lietuvių",
  lv: "Latviešu",
  mt: "Malti",
  nl: "Nederlands",
  pl: "Polski",
  "pt-BR": "Português (Brasil)",
  "pt-PT": "Português (Portugal)",
  rm: "Rumantsch",
  ro: "Română",
  sk: "Slovenčina",
  sl: "Slovenščina",
  sv: "Svenska",
  tr: "Türkçe",
};

const ALIASES: Record<string, string> = {
  ku: "kmr",
  "ku-latn": "kmr",
  "ku-tr": "kmr",
  pt: "pt-BR",
  "pt-ao": "pt-PT",
  "pt-mz": "pt-PT",
  "pt-cv": "pt-PT",
  "pt-gw": "pt-PT",
  "pt-st": "pt-PT",
  "pt-tl": "pt-PT",
  "pt-ch": "pt-PT",
  "pt-lu": "pt-PT",
  "pt-mo": "pt-PT",
  gsw: "de",
  lb: "de",
};

export function suggest(
  preferred: ReadonlyArray<string>,
  available: ReadonlyArray<string>,
): string | null {
  const known = new Map(available.map((tag) => [tag.toLowerCase(), tag]));
  for (const tag of preferred) {
    const lower = tag.toLowerCase();
    const base = lower.split("-", 1)[0] ?? lower;
    const found =
      known.get(lower) ??
      known.get(ALIASES[lower]?.toLowerCase() ?? "") ??
      known.get(ALIASES[base]?.toLowerCase() ?? "") ??
      known.get(base);
    if (found != null) {
      return found;
    }
  }
  return null;
}
