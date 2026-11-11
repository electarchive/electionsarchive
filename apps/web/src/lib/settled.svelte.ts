import { untrack } from "svelte";

export class Settled<T> {
  value = $state.raw<T | null>(null);

  constructor(value: T | Promise<T>) {
    if (value instanceof Promise) {
      void value
        .then((found) => {
          this.value = found;
        })
        .catch(() => null);
    } else {
      this.value = value;
    }
  }
}

export function latest<T>(read: () => T | Promise<T>, key: () => string) {
  let found = $state.raw<{
    from: unknown;
    key: string;
    value: T | null;
  } | null>(null);
  $effect(() => {
    const from = read();
    if (!(from instanceof Promise)) {
      return;
    }
    const at = untrack(key);
    const land = (value: T | null) => {
      if (untrack(read) === from) {
        found = { from, key: at, value };
      }
    };
    void from.then(land).catch(() => {
      land(null);
    });
  });
  const current = $derived.by((): T | null => {
    const from = read();
    if (!(from instanceof Promise)) {
      return from;
    }
    return found != null && (found.from === from || found.key === key())
      ? found.value
      : null;
  });
  return {
    get current() {
      return current;
    },
  };
}
