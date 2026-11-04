import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

const read = async (path) =>
  JSON.parse(await readFile(new URL(path, import.meta.url), "utf8"));

const settings = await read("../project.inlang/settings.json");
const english = await read("../messages/en.json");
const lock = await read("../messages/.source.json").catch(() => ({}));
const digest = (value) =>
  createHash("md5")
    .update(typeof value === "string" ? value : JSON.stringify(value))
    .digest("hex");

const keys = Object.keys(english).filter((key) => key !== "$schema");
const problems = [];
for (const locale of settings.locales) {
  if (locale === settings.baseLocale) {
    continue;
  }
  const messages = await read(`../messages/${locale}.json`).catch(() => null);
  if (messages == null) {
    problems.push(`${locale}: messages/${locale}.json is missing`);
    continue;
  }
  const sources = lock[locale] ?? {};
  const missing = keys.filter((key) => !(key in messages));
  const extra = Object.keys(messages).filter(
    (key) => key !== "$schema" && !(key in english),
  );
  const stale = keys.filter(
    (key) => key in messages && sources[key] !== digest(english[key]),
  );
  for (const [label, list] of [
    ["missing", missing],
    ["not in en.json", extra],
    ["translated from older English", stale],
  ]) {
    if (list.length > 0) {
      problems.push(
        `${locale}: ${list.length} ${label}: ${list.slice(0, 5).join(", ")}${list.length > 5 ? ", …" : ""}`,
      );
    }
  }
}
const tagged = keys.filter((key) =>
  /(?:^|\s)@(?![\p{L}_$])/u.test(
    typeof english[key] === "string"
      ? english[key]
      : JSON.stringify(english[key]),
  ),
);
if (tagged.length > 0) {
  problems.push(
    `en: ${tagged.length} with an @ that paraglide's JSDoc reads as a tag (pass the @ in the input instead): ${tagged.join(", ")}`,
  );
}
if (problems.length > 0) {
  process.exitCode = 1;
  console.error(
    `${problems.join("\n")}\n\nAdd or edit keys in messages/en.json only, then run \`pnpm run translate\` and commit every locale file with messages/.source.json.`,
  );
} else {
  console.log(
    `i18n: ${settings.locales.length} locales match en.json (${keys.length} keys)`,
  );
}
