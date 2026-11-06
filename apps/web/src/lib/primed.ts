import type { QueryClient, QueryKey } from "@tanstack/svelte-query";

export type Primed = Array<{ key: QueryKey; data: unknown }>;

export function prime(client: QueryClient, primed: Primed) {
  for (const { key, data } of primed) {
    if (client.getQueryData(key) === undefined) {
      client.setQueryData(key, data);
    }
  }
}
