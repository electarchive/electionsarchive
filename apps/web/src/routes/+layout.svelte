<script lang="ts">
  // eslint-disable-next-line no-restricted-imports
  import "../app.css";
  import type { Snippet } from "svelte";
  import { onNavigate } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { ProgressBar } from "@prgm/sveltekit-progress-bar";
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import { SvelteQueryDevtools } from "@tanstack/svelte-query-devtools";
  import { ModeWatcher, setMode } from "mode-watcher";
  import { setContext } from "svelte";
  import { on } from "svelte/events";
  import ConfirmationDialog from "#lib/components/confirmation-dialog.svelte";
  import Header from "#lib/components/header.svelte";
  import Provenance from "#lib/components/provenance.svelte";
  import { Toaster } from "#lib/components/ui/sonner/index.js";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { palette } from "#lib/palette.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { makeQueryClient } from "#lib/query-client.js";
  import {
    ConfirmationStore,
    setConfirmationContext,
  } from "#lib/stores/confirmation.svelte.js";

  const { children }: { children: Snippet } = $props();

  const queryClient = makeQueryClient();

  setConfirmationContext(new ConfirmationStore());
  setContext("figures", new Set<string>());

  const embed = $derived(
    /^[a-z]+(?:-\d+)?$/.exec(page.url.searchParams.get("embed") ?? "")?.[0] ??
      null,
  );

  $effect(() => {
    if (embed == null || window.parent === window) {
      return;
    }
    const report = () =>
      window.parent.postMessage(
        {
          type: "ea:height",
          height: Math.ceil(document.body.getBoundingClientRect().height),
        },
        "*",
      );
    const observer = new ResizeObserver(report);
    observer.observe(document.body);
    report();
    const off = on(window, "message", (event) => {
      const data: unknown = event.data;
      if (
        event.source === window.parent &&
        data != null &&
        typeof data === "object" &&
        "type" in data &&
        data.type === "ea:theme" &&
        "mode" in data &&
        (data.mode === "light" || data.mode === "dark")
      ) {
        setMode(data.mode);
      }
    });
    window.parent.postMessage({ type: "ea:ready" }, "*");
    return () => {
      observer.disconnect();
      off();
    };
  });

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
  {#if embed != null}
    <base target="_blank" />
    <meta name="robots" content="noindex" />
  {/if}
</svelte:head>

<QueryClientProvider client={queryClient}>
  <SvelteQueryDevtools />
  <ModeWatcher modeStorageKey={embed == null ? undefined : "ea-embed-mode"} />
  <Toaster />
  <Tooltip.Provider>
    {#if embed == null}
      <div class="grid grid-rows-[auto_1fr] min-block-dvh">
        <Header />
        <div class="mx-auto inline-full max-inline-7xl">
          {@render children()}
        </div>
      </div>
    {:else}
      <div class="p-3.5" data-embed>
        {@render children()}
      </div>
      <a
        class="block px-3.5 pbe-2 text-xs font-semibold text-muted-foreground underline decoration-border underline-offset-4 hover:text-foreground"
        href={resolve("/[...path]", { path: page.url.pathname.slice(1) })}
        >{m.embed_credit()}</a
      >
    {/if}
  </Tooltip.Provider>
  <ProgressBar class="text-primary" displayThresholdMs={300} />
  <ConfirmationDialog />
  <Provenance />
</QueryClientProvider>
