import { mkdir, readFile, writeFile } from "node:fs/promises";
import { shapeSkill } from "#lib/server/skill.ts";

const template = await readFile(
  new URL("../src/lib/server/skill.md", import.meta.url),
  "utf8",
);
const target = new URL("../../../skills/electionsarchive/", import.meta.url);
await mkdir(target, { recursive: true });
await writeFile(
  new URL("SKILL.md", target),
  shapeSkill(template, "read").replaceAll(
    "{{origin}}",
    "https://electionsarchive.com",
  ),
);
