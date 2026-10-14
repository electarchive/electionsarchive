<script lang="ts">
  // eslint-disable-next-line no-restricted-imports
  import "../app.css";
  import type { Snippet } from "svelte";
  import { ProgressBar } from "@prgm/sveltekit-progress-bar";
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import { SvelteQueryDevtools } from "@tanstack/svelte-query-devtools";
  import { ModeWatcher } from "mode-watcher";
  import ConfirmationDialog from "#lib/components/confirmation-dialog.svelte";
  import Header from "#lib/components/header.svelte";
  import { Toaster } from "#lib/components/ui/sonner/index.js";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import {
    ConfirmationStore,
    setConfirmationContext,
  } from "#lib/stores/confirmation.svelte.js";
  import { makeQueryClient } from "#lib/trpc/query-client.js";

  const { children }: { children: Snippet } = $props();

  const queryClient = makeQueryClient();

  setConfirmationContext(new ConfirmationStore());
</script>

<svelte:head>
  <title>Template for SvelteKit projects</title>
  <meta name="description" content="Template for building SvelteKit projects" />
</svelte:head>

<QueryClientProvider client={queryClient}>
  <SvelteQueryDevtools />
  <ModeWatcher />
  <Toaster />
  <Tooltip.Provider>
    <div class="grid grid-rows-[auto_1fr] min-block-dvh">
      <Header />
      <div class="mx-auto inline-full max-inline-7xl">
        {@render children()}
      </div>
    </div>
  </Tooltip.Provider>
  <ProgressBar class="text-primary" displayThresholdMs={300} />
  <ConfirmationDialog />
</QueryClientProvider>
