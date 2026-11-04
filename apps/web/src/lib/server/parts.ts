import type { Fetch } from "svelte-islands";
import { Part } from "svelte-islands";
import { part as wrap } from "svelte-islands/server";

type Made = { name: string; value: unknown; fetch: Fetch };
type Node<T> = {
  objects: WeakMap<object, Node<T>>;
  values: Map<unknown, Node<T>>;
  entry: { value: T; made: Array<Made> } | null;
};

const WIDTH = 8;
const CAPACITY = 256;
const recording: Array<Array<Made>> = [];
const live = new Set<{ entry: unknown }>();

function touch(at: { entry: unknown }) {
  live.delete(at);
  live.add(at);
  for (const oldest of live) {
    if (live.size <= CAPACITY) {
      break;
    }
    live.delete(oldest);
    oldest.entry = null;
  }
}

export function part<T>(
  name: string,
  value: T,
  { fetch = "auto" }: { fetch?: Fetch } = {},
) {
  recording.at(-1)?.push({ name, value, fetch });
  return wrap(name, value, { fetch });
}

function node<T>(): Node<T> {
  return { objects: new WeakMap(), values: new Map(), entry: null };
}

function step<T>(at: Node<T>, key: unknown) {
  if ((typeof key === "object" && key != null) || typeof key === "function") {
    const found = at.objects.get(key) ?? node<T>();
    at.objects.set(key, found);
    return found;
  }
  const found = at.values.get(key) ?? node<T>();
  at.values.delete(key);
  at.values.set(key, found);
  for (const oldest of at.values.keys()) {
    if (at.values.size <= WIDTH) {
      break;
    }
    at.values.delete(oldest);
  }
  return found;
}

export function memo<Keys extends Array<unknown>, T>(
  build: (...keys: Keys) => T,
) {
  const root = node<T>();
  return (...keys: Keys): T => {
    let at = root;
    for (const key of keys) {
      at = step(at, key);
    }
    if (at.entry != null) {
      touch(at);
      for (const { name, value, fetch } of at.entry.made) {
        part(name, value, { fetch });
      }
      return at.entry.value;
    }
    const made: Array<Made> = [];
    recording.push(made);
    try {
      const value = build(...keys);
      at.entry = { value, made };
      touch(at);
      return value;
    } finally {
      recording.pop();
      recording.at(-1)?.push(...made);
    }
  };
}

function shown(found: unknown): unknown {
  if (!(found instanceof Part)) {
    return found;
  }
  const { isInline, loaded, name }: Part = found;
  return isInline ? loaded?.value : name;
}

export function weigh(value: unknown) {
  return (
    JSON.stringify(value, (_, found: unknown) => shown(found))?.length ?? 0
  );
}
