import aliases from "cldr-core/supplemental/aliases.json" with { type: "json" };

const replacements = new Map<string, string>();
for (const [code, { _replacement }] of Object.entries(
  aliases.supplemental.metadata.alias.territoryAlias,
)) {
  const targets = _replacement.split(" ");
  if (targets.length === 1 && targets[0] != null) {
    replacements.set(code, targets[0].toLowerCase());
  }
}

export function canonicalTerritory(code: string) {
  const upper = code.toUpperCase();
  const target = replacements.get(upper) ?? upper.toLowerCase();
  return target === code ? null : target;
}
