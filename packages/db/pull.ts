import { readFileSync, rmSync, writeFileSync } from "node:fs";
import * as app from "./src/schema.ts";

const appEnums = new Set<string>();
for (const value of Object.values(app)) {
  if ("enumName" in value) appEnums.add(value.enumName);
}

const core = readFileSync("pull/schema.ts", "utf8")
  .replaceAll("customType({ dataType: () => 'daterange' })()", "daterange()")
  .split("\n")
  .filter((line) => !appEnums.has(/pgEnum\("([^"]+)"/.exec(line)?.[1] ?? ""))
  .join("\n");
const declared = new Set(
  Array.from(core.matchAll(/^export const (\w+)/gm), (match) => match[1]),
);
const imports = Object.keys(app)
  .filter(
    (name) =>
      !declared.has(name) && new RegExp(String.raw`\b${name}[.(]`).test(core),
  )
  .toSorted((a, b) => a.localeCompare(b));

writeFileSync(
  "src/core.ts",
  `import { daterange } from "./range.ts";\nimport { ${imports.join(", ")} } from "./schema.ts";\n${core}`,
);
writeFileSync(
  "src/core-relations.ts",
  readFileSync("pull/relations.ts", "utf8").replace(
    'import * as schema from "./schema";',
    'import * as core from "./core.ts";\nimport { apikeys, users } from "./schema.ts";\n\nconst schema = { ...core, apikeys, users };',
  ),
);
rmSync("pull", { recursive: true });
