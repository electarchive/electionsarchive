import type { QueryClient, QueryKey } from "@tanstack/svelte-query";

export type Primed = Array<{ key: QueryKey; data: unknown }>;

export function prime(client: QueryClient, primed: Primed | Promise<Primed>) {
  if (primed instanceof Promise) {
    void primed.then((ready) => {
      prime(client, ready);
    });
    return;
  }
  for (const { key, data } of primed) {
    if (client.getQueryData(key) === undefined) {
      client.setQueryData(key, data);
    }
  }
}
