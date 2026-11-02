import type { Plugin, ResolveFn } from "vite";
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { parseAst } from "vite";

const ID = "virtual:code-version";
const RESOLVED = `\0${ID}`;
const PLACEHOLDER = "code-version-placeholder-6c1f0e";
const PROCEDURES = "procedure-versions-placeholder-9a4d27";

const digest = (text: string) =>
  createHash("sha256").update(text).digest("hex");

type Node = { type: string; start: number; end: number } & Record<
  string,
  unknown
>;

type Declaration = { text: string; names: Set<string>; loads: Set<string> };

type Analysis = {
  declarations: Map<string, Declaration>;
  imported: Map<string, string>;
  loaded: Map<string, string>;
  loose: Array<string>;
  procedures: Map<string, Declaration>;
};

const isNode = (value: unknown): value is Node =>
  typeof value === "object" &&
  value != null &&
  "type" in value &&
  typeof value.type === "string";

const KEYED = new Set(["Property", "MethodDefinition", "PropertyDefinition"]);

function scan(node: Node, found: Declaration) {
  if (node.type === "Identifier" && typeof node.name === "string") {
    found.names.add(node.name);
  }
  if (
    node.type === "ImportExpression" &&
    isNode(node.source) &&
    typeof node.source.value === "string"
  ) {
    found.loads.add(node.source.value);
  }
  for (const [field, value] of Object.entries(node)) {
    if (
      node.computed !== true &&
      ((field === "key" && KEYED.has(node.type)) ||
        (field === "property" && node.type === "MemberExpression"))
    ) {
      continue;
    }
    for (const child of [value].flat()) {
      if (isNode(child)) {
        scan(child, found);
      }
    }
  }
}

function declare(code: string, node: Node) {
  const found: Declaration = {
    text: code.slice(node.start, node.end),
    names: new Set(),
    loads: new Set(),
  };
  scan(node, found);
  return found;
}

function bindings(node: unknown): Array<string> {
  if (!isNode(node)) {
    return [];
  }
  if (node.type === "Identifier" && typeof node.name === "string") {
    return [node.name];
  }
  const found: Declaration = { text: "", names: new Set(), loads: new Set() };
  scan(node, found);
  return [...found.names];
}

function keyName(node: Node) {
  if (node.computed === true || !isNode(node.key)) {
    return null;
  }
  if (node.key.type === "Identifier" && typeof node.key.name === "string") {
    return node.key.name;
  }
  return typeof node.key.value === "string" ? node.key.value : null;
}

function collect(
  code: string,
  object: Node,
  prefix: Array<string>,
  into: Map<string, Declaration>,
) {
  for (const property of [object.properties].flat()) {
    if (!isNode(property) || property.type !== "Property") {
      continue;
    }
    const key = keyName(property);
    if (key == null || !isNode(property.value)) {
      continue;
    }
    if (property.value.type === "ObjectExpression") {
      collect(code, property.value, [...prefix, key], into);
    } else {
      into.set([...prefix, key].join("."), declare(code, property));
    }
  }
}

export function codeVersion({
  inputs,
  procedures,
}: {
  inputs: Array<string>;
  procedures: string;
}): Plugin {
  const sources = new Map<string, string>();
  const imports = new Map<string, Array<string>>();
  const analyses = new Map<string, Analysis>();
  const wanted = new Map<string, Map<string, Set<string>>>();
  let folder = "";
  let files = new Set<string>();
  let root = "";
  let isServe = false;
  let manifest = "";
  let resolver: ResolveFn | null = null;
  return {
    name: "code-version",
    enforce: "post",
    applyToEnvironment: (environment) => environment.name === "ssr",
    configResolved(config) {
      root = config.root;
      isServe = config.command === "serve";
      resolver = config.createResolver();
      folder = path.resolve(root, procedures);
      files = new Set(
        readdirSync(folder).map((file) => file.replace(/\.ts$/u, "")),
      );
    },
    resolveId(id) {
      return id === ID ? RESOLVED : null;
    },
    load(id) {
      if (id !== RESOLVED) {
        return null;
      }
      return isServe
        ? "export const codeVersion = null; export const procedureVersions = null;"
        : `export const codeVersion = ${JSON.stringify(PLACEHOLDER)}; export const procedureVersions = ${JSON.stringify(PROCEDURES)};`;
    },
    async transform(code, id) {
      if (isServe) {
        return;
      }
      sources.set(id, digest(code));
      if (id.includes("?")) {
        return;
      }
      const program: Node = { ...parseAst(code) };
      const into = new Map<string, Set<string>>();
      const want = async (source: string, names: Array<string>) => {
        if (!files.has(path.basename(source).replace(/\.[jt]s$/u, ""))) {
          return;
        }
        const target = await resolver?.(source, id, false, true);
        if (target == null || path.dirname(target) !== folder) {
          return;
        }
        const found = into.get(target) ?? new Set();
        for (const name of names) {
          found.add(name);
        }
        into.set(target, found);
      };
      for (const statement of [program.body].flat()) {
        if (!isNode(statement) || !isNode(statement.source)) {
          continue;
        }
        const source = statement.source.value;
        if (typeof source !== "string") {
          continue;
        }
        const names = [statement.specifiers]
          .flat()
          .map((specifier) =>
            isNode(specifier) && specifier.type !== "ImportNamespaceSpecifier"
              ? keyName({
                  ...specifier,
                  key: specifier.imported ?? specifier.local,
                })
              : null,
          );
        await want(
          source,
          statement.type === "ExportAllDeclaration" ||
            names.length === 0 ||
            names.includes(null)
            ? ["*"]
            : names.filter((name) => name != null),
        );
      }
      const dynamic = declare(code, program);
      for (const load of dynamic.loads) {
        await want(load, ["*"]);
      }
      wanted.set(id, into);
      if (path.dirname(id) !== folder) {
        return;
      }
      const analysis: Analysis = {
        declarations: new Map(),
        imported: new Map(),
        loaded: new Map(),
        loose: [],
        procedures: new Map(),
      };
      const resolve = async (source: string) =>
        (await resolver?.(source, id, false, true)) ?? `external:${source}`;
      for (const statement of parseAst(code).body) {
        const node: Node = { ...statement };
        const inner =
          (node.type === "ExportNamedDeclaration" ||
            node.type === "ExportDefaultDeclaration") &&
          isNode(node.declaration)
            ? node.declaration
            : node;
        if (
          inner.type === "ImportDeclaration" &&
          isNode(inner.source) &&
          typeof inner.source.value === "string"
        ) {
          const target = await resolve(inner.source.value);
          for (const specifier of [inner.specifiers].flat()) {
            if (isNode(specifier) && isNode(specifier.local)) {
              for (const name of bindings(specifier.local)) {
                analysis.imported.set(name, target);
              }
            }
          }
        } else if (inner.type === "VariableDeclaration") {
          for (const declarator of [inner.declarations].flat()) {
            if (!isNode(declarator)) {
              continue;
            }
            const found = declare(code, declarator);
            for (const name of bindings(declarator.id)) {
              analysis.declarations.set(name, found);
            }
            if (
              node.type === "ExportNamedDeclaration" &&
              isNode(declarator.init) &&
              declarator.init.type === "ObjectExpression"
            ) {
              collect(
                code,
                declarator.init,
                bindings(declarator.id),
                analysis.procedures,
              );
            }
          }
        } else if (
          (inner.type === "FunctionDeclaration" ||
            inner.type === "ClassDeclaration") &&
          isNode(inner.id)
        ) {
          for (const name of bindings(inner.id)) {
            analysis.declarations.set(name, declare(code, inner));
          }
        } else {
          analysis.loose.push(code.slice(node.start, node.end));
        }
      }
      const loads = new Set<string>();
      for (const found of [
        ...analysis.declarations.values(),
        ...analysis.procedures.values(),
      ]) {
        for (const load of found.loads) {
          loads.add(load);
        }
      }
      for (const load of loads) {
        analysis.loaded.set(load, await resolve(load));
      }
      analyses.set(id, analysis);
    },
    moduleParsed(info) {
      imports.set(info.id, [
        ...info.importedIds,
        ...info.dynamicallyImportedIds,
      ]);
    },
    generateBundle(_options, bundle) {
      const name = (id: string) =>
        path.isAbsolute(id) ? path.relative(root, id) : id;
      const walk = (starts: Array<string>, skip = "") => {
        const queue = [...starts];
        const seen = new Set([...queue, skip]);
        while (queue.length > 0) {
          for (const next of imports.get(queue.pop() ?? "") ?? []) {
            if (!seen.has(next)) {
              seen.add(next);
              queue.push(next);
            }
          }
        }
        return [...seen]
          .filter((id) => id !== skip && id !== "")
          .toSorted((a, b) => a.localeCompare(b, "en"));
      };
      const reach = (starts: Array<string>, skip = "") =>
        walk(starts, skip).map(
          (id) => `${name(id)} ${sources.get(id) ?? "external"}`,
        );
      const modules = new Map<string, string>();
      const closure = (id: string, skip: string) => {
        let found = modules.get(`${skip}|${id}`);
        if (found == null) {
          found = digest(reach([id], skip).join("\n"));
          modules.set(`${skip}|${id}`, found);
        }
        return found;
      };
      const importers = new Map<string, Array<string>>();
      for (const [id, targets] of imports) {
        for (const target of targets) {
          importers.set(target, [...(importers.get(target) ?? []), id]);
        }
      }
      const users = importers.get(RESOLVED) ?? [];
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
      const base = digest(lines.join("\n"));
      lines.push(
        ...reach([
          RESOLVED,
          ...users,
          ...users.flatMap((user) => importers.get(user) ?? []),
        ]),
      );
      const version = digest(lines.join("\n")).slice(0, 16);
      const versions = new Map<string, string>();
      const doubled = new Set<string>();
      for (const [file, analysis] of analyses) {
        for (const [procedure, leaf] of analysis.procedures) {
          const parts = new Set([base, ...analysis.loose, leaf.text]);
          const queue: Array<string> = [];
          const seen = new Set<string>();
          const visited = new Set<string>();
          const enqueue = (names: Iterable<string>) => {
            for (const reference of names) {
              if (!seen.has(reference)) {
                seen.add(reference);
                queue.push(reference);
              }
            }
          };
          const visit = (target: string) => {
            if (visited.has(target)) {
              return;
            }
            visited.add(target);
            parts.add(`${name(target)} ${closure(target, file)}`);
            for (const id of walk([target], file)) {
              const names = wanted.get(id)?.get(file);
              if (names != null) {
                enqueue(names.has("*") ? analysis.declarations.keys() : names);
              }
            }
          };
          const load = (sources: Iterable<string>) => {
            for (const source of sources) {
              visit(analysis.loaded.get(source) ?? `external:${source}`);
            }
          };
          enqueue(leaf.names);
          load(leaf.loads);
          while (queue.length > 0) {
            const next = queue.pop() ?? "";
            const local = analysis.declarations.get(next);
            const target = analysis.imported.get(next);
            if (local != null) {
              parts.add(local.text);
              load(local.loads);
              enqueue(local.names);
            } else if (target != null) {
              visit(target);
            }
          }
          if (versions.has(procedure)) {
            doubled.add(procedure);
          }
          versions.set(
            procedure,
            digest(
              [...parts]
                .toSorted((a, b) => a.localeCompare(b, "en"))
                .join("\n"),
            ).slice(0, 16),
          );
        }
      }
      for (const procedure of doubled) {
        versions.delete(procedure);
      }
      const table = Object.fromEntries(
        [...versions].toSorted(([a], [b]) => a.localeCompare(b, "en")),
      );
      const encoded = JSON.stringify(JSON.stringify(table)).slice(1, -1);
      for (const chunk of Object.values(bundle)) {
        if (chunk.type === "chunk" && chunk.code.includes(PLACEHOLDER)) {
          chunk.code = chunk.code
            .replaceAll(PLACEHOLDER, () => version)
            .replaceAll(PROCEDURES, () => encoded);
        }
      }
      manifest = `${version}\n${lines.join("\n")}\n${Object.entries(table)
        .map(([procedure, hash]) => `procedure ${procedure} ${hash}\n`)
        .join("")}`;
    },
    writeBundle(options) {
      if (options.dir != null) {
        writeFileSync(path.join(options.dir, "code-version.txt"), manifest);
      }
    },
  };
}
