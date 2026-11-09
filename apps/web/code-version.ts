import type { Plugin, ResolveFn } from "vite";
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { isBuiltin } from "node:module";
import path from "node:path";
import { parseAst } from "vite";

const ID = "virtual:code-version";
const RESOLVED = `\0${ID}`;
const PLACEHOLDER = "code-version-placeholder-6c1f0e";
const PROCEDURES = "procedure-versions-placeholder-9a4d27";
const SITE = "remember-version-placeholder-5b0c2e-";
const UNDATED = "-undated";
const DATED =
  /current_date|current_timestamp|localtimestamp|clock_timestamp|(?<!performance\.)\bnow\(|\bage\(|'(?:now|today|tomorrow|yesterday)'|new Date\(\)|Temporal\.Now/iu;

const digest = (text: string) =>
  createHash("sha256").update(text).digest("hex");

type Node = { type: string; start: number; end: number } & Record<
  string,
  unknown
>;

type Declaration = {
  text: string;
  names: Set<string>;
  loads: Set<string>;
  start: number;
  end: number;
};

type Analysis = {
  declarations: Map<string, Declaration>;
  exported: Set<string>;
  imported: Map<string, { target: string; name: string | null }>;
  loaded: Map<string, string>;
  loose: Array<string>;
  procedures: Map<string, Declaration>;
  sites: Map<number, { unit: string; leaf: Declaration }>;
};

const isNode = (value: unknown): value is Node =>
  typeof value === "object" &&
  value != null &&
  "type" in value &&
  typeof value.type === "string";

const KEYED = new Set(["Property", "MethodDefinition", "PropertyDefinition"]);

const FUNCTIONS = new Set([
  "ArrowFunctionExpression",
  "FunctionDeclaration",
  "FunctionExpression",
]);

const BLOCKS = new Set(["BlockStatement", "Program", "StaticBlock"]);

const LOOPS = new Set(["ForInStatement", "ForOfStatement", "ForStatement"]);

function patternNames(node: unknown): Array<string> {
  const found: Array<string> = [];
  const stack = [node].flat();
  while (stack.length > 0) {
    const next = stack.pop();
    if (!isNode(next)) {
      continue;
    }
    if (next.type === "Identifier" && typeof next.name === "string") {
      found.push(next.name);
    } else if (next.type === "AssignmentPattern") {
      stack.push(next.left);
    } else if (next.type === "RestElement") {
      stack.push(next.argument);
    } else if (next.type === "Property") {
      stack.push(next.value);
    } else if (next.type === "ArrayPattern") {
      stack.push(...[next.elements].flat());
    } else if (next.type === "ObjectPattern") {
      stack.push(...[next.properties].flat());
    }
  }
  return found;
}

function declared(statements: Array<unknown>) {
  const found: Array<string> = [];
  for (const statement of statements) {
    if (!isNode(statement)) {
      continue;
    }
    if (statement.type === "VariableDeclaration") {
      for (const declarator of [statement.declarations].flat()) {
        if (isNode(declarator)) {
          found.push(...patternNames(declarator.id));
        }
      }
    } else if (
      statement.type === "FunctionDeclaration" ||
      statement.type === "ClassDeclaration"
    ) {
      found.push(...patternNames(statement.id));
    }
  }
  return found;
}

function scope(node: Node, bound: ReadonlySet<string>) {
  const names: Array<string> = [];
  if (FUNCTIONS.has(node.type)) {
    names.push(...patternNames(node.params));
    if (node.type === "FunctionExpression") {
      names.push(...patternNames(node.id));
    }
  } else if (node.type === "ClassExpression") {
    names.push(...patternNames(node.id));
  } else if (BLOCKS.has(node.type)) {
    names.push(...declared([node.body].flat()));
  } else if (node.type === "SwitchStatement") {
    names.push(
      ...declared(
        [node.cases]
          .flat()
          .flatMap((entry) => (isNode(entry) ? [entry.consequent].flat() : [])),
      ),
    );
  } else if (LOOPS.has(node.type)) {
    names.push(...declared([node.init ?? node.left]));
  } else if (node.type === "CatchClause") {
    names.push(...patternNames(node.param));
  }
  return names.length === 0 ? bound : new Set([...bound, ...names]);
}

function scan(
  node: Node,
  found: Declaration,
  outer: ReadonlySet<string> = new Set(),
) {
  const bound = scope(node, outer);
  if (
    node.type === "Identifier" &&
    typeof node.name === "string" &&
    !bound.has(node.name)
  ) {
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
        scan(child, found, bound);
      }
    }
  }
}

function declare(code: string, node: Node) {
  const found: Declaration = {
    text: code.slice(node.start, node.end),
    names: new Set(),
    loads: new Set(),
    start: node.start,
    end: node.end,
  };
  scan(node, found);
  return found;
}

function calls(node: Node, into: Array<Node>) {
  if (
    node.type === "CallExpression" &&
    isNode(node.callee) &&
    node.callee.type === "Identifier" &&
    node.callee.name === "remember"
  ) {
    into.push(node);
  }
  for (const value of Object.values(node)) {
    for (const child of [value].flat()) {
      if (isNode(child)) {
        calls(child, into);
      }
    }
  }
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

function snapshots(text: string) {
  const found = new Map<string, Array<string>>();
  let isInside = false;
  let current: Array<string> | null = null;
  let isListing = false;
  const unquote = (value: string) => value.replace(/^'(.*)'$/u, "$1");
  for (const line of text.split("\n")) {
    if (line === "") {
      continue;
    }
    if (!line.startsWith(" ")) {
      isInside = line === "snapshots:";
      current = null;
      continue;
    }
    if (!isInside) {
      continue;
    }
    const entry = /^ {2}(\S.*?):(?: \{\})?$/u.exec(line);
    if (entry?.[1] != null) {
      current = [];
      found.set(unquote(entry[1]), current);
      continue;
    }
    const section = /^ {4}(\w+):$/u.exec(line);
    if (section?.[1] != null) {
      isListing =
        section[1] === "dependencies" || section[1] === "optionalDependencies";
      continue;
    }
    const dependency = /^ {6}(\S+): (\S+)$/u.exec(line);
    if (
      isListing &&
      current != null &&
      dependency?.[1] != null &&
      dependency[2] != null &&
      !dependency[2].startsWith("link:")
    ) {
      current.push(`${unquote(dependency[1])}@${unquote(dependency[2])}`);
    }
  }
  return found;
}

function packageOf(id: string) {
  const marker = "/node_modules/";
  const at = id.lastIndexOf(marker);
  const tail =
    at === -1 ? id.replace(/^external:/u, "") : id.slice(at + marker.length);
  const parts = tail.split("/");
  return tail.startsWith("@")
    ? parts.slice(0, 2).join("/")
    : (parts[0] ?? tail);
}

const isOwn = (id: string) =>
  path.isAbsolute(id) && !id.includes("/node_modules/");

const isAnalyzed = (id: string) => isOwn(id) && /\.[jt]s$/u.test(id);

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
  lockfile,
  procedures,
  stamps,
}: {
  inputs: Array<string>;
  lockfile: string;
  procedures: string;
  stamps: Array<string>;
}): Plugin {
  const sources = new Map<string, string>();
  const dated = new Set<string>();
  let clocked = new Set<string>();
  const imports = new Map<string, Array<string>>();
  const analyses = new Map<string, Analysis>();
  const wanted = new Map<string, Map<string, Set<string>>>();
  let folder = "";
  let root = "";
  let isServe = false;
  let manifest = "";
  let site = 0;
  let resolver: ResolveFn | null = null;
  let locked = new Map<string, Array<string>>();
  const packages = new Map<string, string>();
  const packaged = (id: string) => {
    const name = packageOf(id);
    if (isBuiltin(name)) {
      return "builtin";
    }
    let found = packages.get(name);
    if (found == null) {
      const queue = [...locked.keys()].filter(
        (key) => key.slice(0, key.indexOf("@", 1)) === name,
      );
      const seen = new Set(queue);
      while (queue.length > 0) {
        for (const next of locked.get(queue.pop() ?? "") ?? []) {
          if (!seen.has(next)) {
            seen.add(next);
            queue.push(next);
          }
        }
      }
      found =
        seen.size === 0
          ? "unlocked"
          : `locked:${digest([...seen].toSorted((a, b) => a.localeCompare(b, "en")).join("\n")).slice(0, 16)}`;
      packages.set(name, found);
    }
    return found;
  };
  return {
    name: "code-version",
    enforce: "post",
    applyToEnvironment: (environment) => environment.name === "ssr",
    configResolved(config) {
      root = config.root;
      isServe = config.command === "serve";
      resolver = config.createResolver();
      folder = path.resolve(root, procedures);
      locked = snapshots(readFileSync(path.resolve(root, lockfile), "utf8"));
      packages.clear();
      clocked = new Set(stamps.map((stamp) => path.resolve(root, stamp)));
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
      if (isOwn(id) && !clocked.has(id) && DATED.test(code)) {
        dated.add(id);
      }
      if (id.includes("?")) {
        return;
      }
      const program: Node = { ...parseAst(code) };
      const into = new Map<string, Set<string>>();
      const want = async (source: string, names: Array<string>) => {
        const target = await resolver?.(source, id, false, true);
        if (target == null || !isAnalyzed(target)) {
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
      if (!isAnalyzed(id)) {
        return;
      }
      const analysis: Analysis = {
        declarations: new Map(),
        exported: new Set(),
        imported: new Map(),
        loaded: new Map(),
        loose: [],
        procedures: new Map(),
        sites: new Map(),
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
              const imported =
                specifier.type === "ImportSpecifier"
                  ? keyName({ ...specifier, key: specifier.imported })
                  : null;
              for (const name of patternNames(specifier.local)) {
                analysis.imported.set(name, { target, name: imported });
              }
            }
          }
        } else if (inner.type === "VariableDeclaration") {
          for (const declarator of [inner.declarations].flat()) {
            if (!isNode(declarator)) {
              continue;
            }
            const found = declare(code, declarator);
            for (const name of patternNames(declarator.id)) {
              analysis.declarations.set(name, found);
              if (node.type === "ExportNamedDeclaration") {
                analysis.exported.add(name);
              }
            }
            if (
              path.dirname(id) === folder &&
              node.type === "ExportNamedDeclaration" &&
              isNode(declarator.init) &&
              declarator.init.type === "ObjectExpression"
            ) {
              collect(
                code,
                declarator.init,
                patternNames(declarator.id),
                analysis.procedures,
              );
            }
          }
        } else if (
          (inner.type === "FunctionDeclaration" ||
            inner.type === "ClassDeclaration") &&
          isNode(inner.id)
        ) {
          for (const name of patternNames(inner.id)) {
            analysis.declarations.set(name, declare(code, inner));
            if (node.type === "ExportNamedDeclaration") {
              analysis.exported.add(name);
            }
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
      if (!analysis.imported.has("remember")) {
        return;
      }
      const units = [...analysis.declarations, ...analysis.procedures].map(
        ([unit, leaf]) => ({ unit, leaf }),
      );
      const found: Array<Node> = [];
      calls(program, found);
      const inserts: Array<{ start: number; end: number; text: string }> = [];
      for (const call of found) {
        const args = [call.arguments].flat().filter(isNode);
        const last = args.at(-1);
        const compute = args[1]?.type;
        const generation = args[3];
        const owner = units
          .filter(
            ({ leaf }) => leaf.start <= call.start && call.end <= leaf.end,
          )
          .toSorted(
            (a, b) => a.leaf.end - a.leaf.start - (b.leaf.end - b.leaf.start),
          )[0];
        if (
          owner == null ||
          last == null ||
          args.length < 2 ||
          args.length > 5 ||
          (generation != null &&
            (generation.type !== "Identifier" ||
              generation.name !== "undefined")) ||
          args.some((arg) => arg.type === "SpreadElement") ||
          (compute !== "ArrowFunctionExpression" &&
            compute !== "FunctionExpression")
        ) {
          continue;
        }
        analysis.sites.set(site, owner);
        const placeholder = JSON.stringify(`${SITE}${String(site)}`);
        inserts.push(
          generation == null
            ? {
                start: last.end,
                end: last.end,
                text: `${args.length === 2 ? ", false" : ""}, ${placeholder}`,
              }
            : {
                start: generation.start,
                end: generation.end,
                text: placeholder,
              },
        );
        site += 1;
      }
      if (inserts.length === 0) {
        return;
      }
      let edited = code;
      for (const { start, end, text } of inserts.toSorted(
        (a, b) => b.start - a.start,
      )) {
        edited = `${edited.slice(0, start)}${text}${edited.slice(end)}`;
      }
      return { code: edited, map: null };
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
          (id) => `${name(id)} ${sources.get(id) ?? packaged(id)}`,
        );
      const importers = new Map<string, Array<string>>();
      for (const [id, targets] of imports) {
        for (const target of targets) {
          importers.set(target, [...(importers.get(target) ?? []), id]);
        }
      }
      const users = importers.get(RESOLVED) ?? [];
      const reads = (id: string, skip: string) => {
        const queue = isOwn(id) ? [id] : [];
        const seen = new Set([...queue, skip, ...users]);
        while (queue.length > 0) {
          const next = queue.pop() ?? "";
          if (dated.has(next)) {
            return true;
          }
          for (const target of imports.get(next) ?? []) {
            if (!seen.has(target) && isOwn(target)) {
              seen.add(target);
              queue.push(target);
            }
          }
        }
        return false;
      };
      const modules = new Map<string, { hashed: string; isDated: boolean }>();
      const closure = (id: string, skip: string) => {
        let found = modules.get(`${skip}|${id}`);
        if (found == null) {
          found = {
            hashed: digest(reach([id], skip).join("\n")),
            isDated: reads(id, skip),
          };
          modules.set(`${skip}|${id}`, found);
        }
        return found;
      };
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
      const hash = (origin: string, leaf: Declaration) => {
        const parts = new Set([base]);
        const queue: Array<[string, string]> = [];
        const seen = new Set<string>();
        const visited = new Set<string>();
        const touched = new Set<string>();
        let isDated = false;
        const enqueue = (file: string, names: Iterable<string>) => {
          for (const reference of names) {
            const key = `${file}\0${reference}`;
            if (!seen.has(key)) {
              seen.add(key);
              queue.push([file, reference]);
            }
          }
        };
        const visit = (file: string, target: string) => {
          const key = `${file}\0${target}`;
          if (visited.has(key)) {
            return;
          }
          visited.add(key);
          const found = closure(target, file);
          isDated ||= found.isDated;
          parts.add(`${name(target)} ${found.hashed}`);
          for (const id of walk([target], file)) {
            const names = wanted.get(id)?.get(file);
            if (names != null) {
              enqueue(
                file,
                names.has("*")
                  ? (analyses.get(file)?.declarations.keys() ?? [])
                  : names,
              );
            }
          }
        };
        const take = (file: string, found: Declaration) => {
          const analysis = analyses.get(file);
          const isClocked = clocked.has(file) || users.includes(file);
          if (!touched.has(file)) {
            touched.add(file);
            for (const text of analysis?.loose ?? []) {
              isDated ||= !isClocked && DATED.test(text);
              parts.add(`${name(file)}\n${text}`);
            }
          }
          isDated ||= !isClocked && DATED.test(found.text);
          parts.add(`${name(file)}\n${found.text}`);
          for (const source of found.loads) {
            visit(file, analysis?.loaded.get(source) ?? `external:${source}`);
          }
          enqueue(file, found.names);
        };
        take(origin, leaf);
        while (queue.length > 0) {
          const [file, next] = queue.pop() ?? ["", ""];
          const analysis = analyses.get(file);
          const local = analysis?.declarations.get(next);
          const imported = analysis?.imported.get(next);
          if (local != null) {
            take(file, local);
          } else if (
            imported?.name != null &&
            analyses.get(imported.target)?.exported.has(imported.name) === true
          ) {
            enqueue(imported.target, [imported.name]);
          } else if (imported != null) {
            visit(file, imported.target);
          }
        }
        const hashed = digest(
          [...parts].toSorted((a, b) => a.localeCompare(b, "en")).join("\n"),
        ).slice(0, 16);
        return isDated ? hashed : `${hashed}${UNDATED}`;
      };
      const versions = new Map<string, string>();
      const doubled = new Set<string>();
      const sites = new Map<number, string>();
      const remembered = new Set<string>();
      for (const [file, analysis] of analyses) {
        for (const [procedure, leaf] of analysis.procedures) {
          if (versions.has(procedure)) {
            doubled.add(procedure);
          }
          versions.set(procedure, hash(file, leaf));
        }
        const owners = new Map<Declaration, string>();
        for (const [id, { unit, leaf }] of analysis.sites) {
          const found = owners.get(leaf) ?? hash(file, leaf);
          owners.set(leaf, found);
          sites.set(id, found);
          remembered.add(`remember ${name(file)} ${unit} ${found}\n`);
        }
      }
      for (const procedure of doubled) {
        versions.delete(procedure);
      }
      const table = Object.fromEntries(
        [...versions].toSorted(([a], [b]) => a.localeCompare(b, "en")),
      );
      const encoded = JSON.stringify(JSON.stringify(table)).slice(1, -1);
      const placed = new RegExp(String.raw`${SITE}(\d+)`, "gu");
      for (const chunk of Object.values(bundle)) {
        if (chunk.type === "chunk") {
          chunk.code = chunk.code
            .replaceAll(PLACEHOLDER, () => version)
            .replaceAll(PROCEDURES, () => encoded)
            .replaceAll(
              placed,
              (_match, id: string) => sites.get(Number(id)) ?? version,
            );
        }
      }
      manifest = `${version}\n${lines.join("\n")}\n${Object.entries(table)
        .map(([procedure, found]) => `procedure ${procedure} ${found}\n`)
        .join(
          "",
        )}${[...remembered].toSorted((a, b) => a.localeCompare(b, "en")).join("")}`;
    },
    writeBundle(options) {
      if (options.dir != null) {
        writeFileSync(path.join(options.dir, "code-version.txt"), manifest);
      }
    },
  };
}
