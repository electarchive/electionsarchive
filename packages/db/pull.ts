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

writeFileSync(
  "src/core.ts",
  `import { daterange } from "./range.ts";\nimport { users } from "./schema.ts";\n${core}`,
);
writeFileSync(
  "src/core-relations.ts",
  readFileSync("pull/relations.ts", "utf8").replace(
    'from "./schema"',
    'from "./core.ts"',
  ),
);
rmSync("pull", { recursive: true });
