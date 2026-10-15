import { writeFileSync } from "node:fs";
import { rulesJsonSchema } from "./src/rules.ts";

writeFileSync(
  new URL("rules.schema.json", import.meta.url),
  `${JSON.stringify(rulesJsonSchema, null, 2)}\n`,
);
