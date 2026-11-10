import type { Plugin } from "vite";
import { readFileSync } from "node:fs";
import path from "node:path";
import { parseAst } from "vite";

const PREFIX = "\0client-message/";
const LOCALIZE = "\0client-message-localize";
const EXPORT = /^export const (\w+) =/gmu;
const ALIAS = /^export \{ (\w+) as "(\w+)" \}$/gmu;

type Node = { type: string; start: number; end: number } & Record<
  string,
  unknown
>;

const isNode = (value: unknown): value is Node =>
  value != null && typeof value === "object" && "type" in value;

function english(file: string) {
  const code = readFileSync(file, "utf8");
  const messages = new Map<string, string>();
  let registry = "registry";
  for (const statement of parseAst(code).body) {
    if (statement.type === "ImportDeclaration") {
      for (const specifier of statement.specifiers) {
        if (specifier.type === "ImportNamespaceSpecifier") {
          registry = specifier.local.name;
        }
      }
      continue;
    }
    if (
      statement.type !== "ExportNamedDeclaration" ||
      statement.declaration?.type !== "VariableDeclaration"
    ) {
      continue;
    }
    for (const declarator of statement.declaration.declarations) {
      const { id, init } = declarator;
      if (id.type === "Identifier" && isNode(init)) {
        messages.set(id.name, code.slice(init.start, init.end));
      }
    }
  }
  return { messages, registry };
}

export function clientMessages(): Plugin {
  let directory = "";
  let messages = new Map<string, string>();
  let registry = "registry";
  return {
    name: "client-messages",
    apply: "build",
    enforce: "pre",
    applyToEnvironment: (environment) =>
      environment.config.consumer === "client",
    transform(code, id) {
      if (!id.endsWith("/src/lib/paraglide/messages/_index.js")) {
        return null;
      }
      directory = path.dirname(id);
      ({ messages, registry } = english(path.join(directory, "en.js")));
      const lines = [];
      for (const [, name] of code.matchAll(EXPORT)) {
        if (name == null || !messages.has(name)) {
          throw new Error(`client-messages: no English message ${name}`);
        }
        lines.push(`export * from "${PREFIX}${name}"`);
      }
      for (const [, name, alias] of code.matchAll(ALIAS)) {
        lines.push(`export { ${name} as "${alias}" } from "${PREFIX}${name}"`);
      }
      return { code: lines.join("\n"), map: null };
    },
    resolveId(id) {
      return id === LOCALIZE || id.startsWith(PREFIX) ? id : null;
    },
    load(id) {
      if (id === LOCALIZE) {
        const dictionary = path.join(directory, "../../dictionary.ts");
        return [
          `import { dictionary } from ${JSON.stringify(dictionary)}`,
          `export const localize = (name, en) => (inputs = {}, options = {}) => dictionary.messages == null || options.locale === "en" ? en(inputs) : dictionary.messages[name](inputs)`,
        ].join("\n");
      }
      if (!id.startsWith(PREFIX)) {
        return null;
      }
      const name = id.slice(PREFIX.length);
      const message = messages.get(name);
      if (message == null) {
        throw new Error(`client-messages: no English message ${name}`);
      }
      const code = [
        `import * as ${registry} from ${JSON.stringify(path.join(directory, "../registry.js"))}`,
        `import { localize } from "${LOCALIZE}"`,
        `export const ${name} = /* @__PURE__ */ localize("${name}", ${message})`,
      ].join("\n");
      return { code, moduleSideEffects: false };
    },
  };
}
