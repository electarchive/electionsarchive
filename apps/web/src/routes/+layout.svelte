<script lang="ts">
  // eslint-disable-next-line no-restricted-imports
  import "../app.css";
  import type { Snippet } from "svelte";
  import { onNavigate } from "$app/navigation";
  import { ProgressBar } from "@prgm/sveltekit-progress-bar";
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import { SvelteQueryDevtools } from "@tanstack/svelte-query-devtools";
  import { ModeWatcher } from "mode-watcher";
  import ConfirmationDialog from "#lib/components/confirmation-dialog.svelte";
  import Header from "#lib/components/header.svelte";
  import { Toaster } from "#lib/components/ui/sonner/index.js";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { palette } from "#lib/palette.svelte.js";
  import { makeQueryClient } from "#lib/query-client.js";
  import {
    ConfirmationStore,
    setConfirmationContext,
  } from "#lib/stores/confirmation.svelte.js";

  const { children }: { children: Snippet } = $props();

  const queryClient = makeQueryClient();

  setConfirmationContext(new ConfirmationStore());

  $effect(() => {
    try {
      const saved: unknown = JSON.parse(
        localStorage.getItem("palette") ?? "null",
      );
      if (saved != null && typeof saved === "object") {
        if ("isSafe" in saved && typeof saved.isSafe === "boolean") {
          palette.isSafe = saved.isSafe;
        }
        if (
          "overrides" in saved &&
          saved.overrides != null &&
          typeof saved.overrides === "object"
        ) {
          for (const [party, color] of Object.entries(saved.overrides)) {
            if (typeof color === "string" && /^#[\da-f]{6}$/i.test(color)) {
              palette.overrides[party] = color;
            }
          }
        }
      }
    } catch {}
  });

  $effect(() => {
    const value = JSON.stringify($state.snapshot(palette));
    try {
      localStorage.setItem("palette", value);
    } catch {}
  });

  onNavigate(async (navigation) => {
    if (
      !("startViewTransition" in document) ||
      navigation.from?.url.pathname === navigation.to?.url.pathname ||
      matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      return;
    }
    const source =
      document.querySelector<HTMLElement>("[data-scope-source]") ??
      document.querySelector<HTMLElement>("[data-scope-title]");
    for (const element of document.querySelectorAll<HTMLElement>(
      "[data-scope-title], [data-scope-source]",
    )) {
      element.style.viewTransitionName =
        element === source ? "scope-title" : "";
      delete element.dataset.scopeSource;
    }
    return new Promise((resolve) => {
      document.startViewTransition(async () => {
        resolve();
        await navigation.complete;
        const title = document.querySelector<HTMLElement>("[data-scope-title]");
        source?.style.removeProperty("view-transition-name");
        for (const element of document.querySelectorAll<HTMLElement>(
          "[data-scope-title]",
        )) {
          element.style.viewTransitionName =
            element === title ? "scope-title" : "";
        }
      });
    });
  });
</script>

<svelte:head>
  <title>electionsarchive.com</title>
  <meta
    name="description"
    content="Every election in ten polities, down to the finest reporting unit, with a source for every number."
  />
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
