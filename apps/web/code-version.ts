import type { Plugin } from "vite";
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";

const ID = "virtual:code-version";
const RESOLVED = `\0${ID}`;
const PLACEHOLDER = "code-version-placeholder-6c1f0e";

const digest = (text: string) =>
  createHash("sha256").update(text).digest("hex");

export function codeVersion({ inputs }: { inputs: Array<string> }): Plugin {
  const sources = new Map<string, string>();
  const imports = new Map<string, Array<string>>();
  let root = "";
  let isServe = false;
  let manifest = "";
  return {
    name: "code-version",
    enforce: "post",
    applyToEnvironment: (environment) => environment.name === "ssr",
    configResolved(config) {
      root = config.root;
      isServe = config.command === "serve";
    },
    resolveId(id) {
      return id === ID ? RESOLVED : null;
    },
    load(id) {
      if (id !== RESOLVED) {
        return null;
      }
      return `export const codeVersion = ${isServe ? "null" : JSON.stringify(PLACEHOLDER)};`;
    },
    transform(code, id) {
      if (!isServe) {
        sources.set(id, digest(code));
      }
      return null;
    },
    moduleParsed(info) {
      imports.set(info.id, [
        ...info.importedIds,
        ...info.dynamicallyImportedIds,
      ]);
    },
    generateBundle(_options, bundle) {
      const importers = new Map<string, Array<string>>();
      for (const [id, targets] of imports) {
        for (const target of targets) {
          importers.set(target, [...(importers.get(target) ?? []), id]);
        }
      }
      const users = importers.get(RESOLVED) ?? [];
      const queue = [
        ...users,
        ...users.flatMap((user) => importers.get(user) ?? []),
      ];
      const seen = new Set([RESOLVED, ...queue]);
      while (queue.length > 0) {
        for (const next of imports.get(queue.pop() ?? "") ?? []) {
          if (!seen.has(next)) {
            seen.add(next);
            queue.push(next);
          }
        }
      }
      const lines: Array<string> = [];
      for (const input of inputs) {
        const full = path.resolve(root, input);
        const files = statSync(full).isDirectory()
          ? readdirSync(full, { recursive: true, withFileTypes: true })
              .filter((entry) => entry.isFile())
              .map((entry) => path.join(entry.parentPath, entry.name))
              .toSorted((a, b) => a.localeCompare(b, "en"))
          : [full];
        for (const file of files) {
          lines.push(
            `${path.relative(root, file)} ${digest(readFileSync(file, "utf8"))}`,
          );
        }
      }
      for (const id of [...seen].toSorted((a, b) => a.localeCompare(b, "en"))) {
        const name = path.isAbsolute(id) ? path.relative(root, id) : id;
        lines.push(`${name} ${sources.get(id) ?? "external"}`);
      }
      const version = digest(lines.join("\n")).slice(0, 16);
      for (const chunk of Object.values(bundle)) {
        if (chunk.type === "chunk" && chunk.code.includes(PLACEHOLDER)) {
          chunk.code = chunk.code.replaceAll(PLACEHOLDER, () => version);
        }
      }
      manifest = `${version}\n${lines.join("\n")}\n`;
    },
    writeBundle(options) {
      if (options.dir != null) {
        writeFileSync(path.join(options.dir, "code-version.txt"), manifest);
      }
    },
  };
}
