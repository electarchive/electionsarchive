<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { m } from "#lib/paraglide/messages.js";
  import { useTRPC } from "#lib/trpc/client.svelte.js";

  const trpc = useTRPC();

  const query = createQuery(() => ({
    queryKey: ["greeting"],
    queryFn: async () => trpc.example.greeting.query(),
  }));
</script>

<main class="grid content-start gap-4 px-3 py-1.5">
  <h1 class="text-2xl font-bold">{m.title()}</h1>
  {#if query.isLoading}
    <div>Loading...</div>
  {:else if query.isError}
    <div>Error: {query.error.message}</div>
  {:else}
    <div>{query.data}</div>
  {/if}
</main>
