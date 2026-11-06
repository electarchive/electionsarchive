import type { Plugin } from "vite";
import { Buffer } from "node:buffer";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const ID = "virtual:view-preloads";

const TOKEN = "__EA_VIEW_PRELOADS__";

export function viewPreloads({ views }: { views: Record<string, string> }) {
  let found: Record<string, Array<string>> = {};
  const module = {
    name: "view-preloads:module",
    resolveId(id) {
      return id === ID ? `\0${ID}` : undefined;
    },
    load(id) {
      return id === `\0${ID}`
        ? `function decode() {\n  try {\n    return JSON.parse(atob(${JSON.stringify(TOKEN)}));\n  } catch {\n    return {};\n  }\n}\nexport const preloads = decode();\n`
        : undefined;
    },
  } satisfies Plugin;
  const collect = {
    name: "view-preloads",
    applyToEnvironment: (environment) => environment.name === "client",
    generateBundle(_options, bundle) {
      const chunks = new Map<string, { imports: Array<string> }>();
      const entries = new Map<string, string>();
      for (const output of Object.values(bundle)) {
        if (output.type === "chunk") {
          chunks.set(output.fileName, output);
          if (output.facadeModuleId != null) {
            entries.set(output.facadeModuleId, output.fileName);
          }
        }
      }
      found = {};
      for (const [name, source] of Object.entries(views)) {
        const start = entries.get(path.resolve(source));
        if (start == null) {
          throw new Error(`view-preloads: no client chunk for ${source}`);
        }
        const seen = new Set<string>();
        const pending = [start];
        for (let file = pending.pop(); file != null; file = pending.pop()) {
          if (!seen.has(file)) {
            seen.add(file);
            pending.push(...(chunks.get(file)?.imports ?? []));
          }
        }
        found[name] = [...seen].map((file) => `/${file}`);
      }
    },
    writeBundle(options) {
      if (options.dir == null) {
        return;
      }
      const packed = Buffer.from(JSON.stringify(found)).toString("base64");
      const server = path.join(options.dir, "..", "server");
      let patched = 0;
      for (const entry of readdirSync(server, {
        recursive: true,
        withFileTypes: true,
      })) {
        if (entry.isFile() && entry.name.endsWith(".js")) {
          const file = path.join(entry.parentPath, entry.name);
          const code = readFileSync(file, "utf8");
          if (code.includes(TOKEN)) {
            writeFileSync(
              file,
              code.replaceAll(TOKEN, () => packed),
            );
            patched += 1;
          }
        }
      }
      if (patched === 0) {
        throw new Error("view-preloads: no server module to patch");
      }
    },
  } satisfies Plugin;
  return [module, collect];
}
