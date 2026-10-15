import { browser } from "$app/env";
import {
  defaultShouldDehydrateQuery,
  MutationCache,
  QueryCache,
  QueryClient,
} from "@tanstack/svelte-query";
import { toast } from "svelte-sonner";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        enabled: browser,
        staleTime: Infinity,
        refetchOnMount: false,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        retry: false,
        retryOnMount: false,
        throwOnError: false,
      },
      mutations: { retry: false, throwOnError: false },
      dehydrate: {
        shouldDehydrateQuery: (query) =>
          defaultShouldDehydrateQuery(query) ||
          query.state.status === "pending",
      },
    },
    queryCache: new QueryCache({
      onError: (error) => {
        console.error(error);

        toast.dismiss();
        toast.error(error.message || "an unknown error occurred", {
          duration: 7000,
        });
      },
    }),
    mutationCache: new MutationCache({
      onError: (error) => {
        console.error(error);

        toast.dismiss();
        toast.error(error.message || "an unknown error occurred", {
          duration: 7000,
        });
      },
    }),
  });
}
