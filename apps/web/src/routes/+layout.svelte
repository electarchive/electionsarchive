<script lang="ts">
  // eslint-disable-next-line no-restricted-imports
  import "../app.css";
  import type { Snippet } from "svelte";
  import { onNavigate } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { navigating, page } from "$app/state";
  import { ProgressBar } from "@prgm/sveltekit-progress-bar";
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import { SvelteQueryDevtools } from "@tanstack/svelte-query-devtools";
  import { ModeWatcher, setMode } from "mode-watcher";
  import { setContext } from "svelte";
  import { on } from "svelte/events";
  import ConfirmationDialog from "#lib/components/confirmation-dialog.svelte";
  import Explain from "#lib/components/explain.svelte";
  import Header from "#lib/components/header.svelte";
  import Provenance from "#lib/components/provenance.svelte";
  import Shahmaran from "#lib/components/shahmaran.svelte";
  import { Toaster } from "#lib/components/ui/sonner/index.js";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { palette } from "#lib/palette.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { makeQueryClient } from "#lib/query-client.js";
  import {
    ConfirmationStore,
    setConfirmationContext,
  } from "#lib/stores/confirmation.svelte.js";
  import { cn } from "#lib/utils.js";

  const { children }: { children: Snippet } = $props();

  const queryClient = makeQueryClient();

  setConfirmationContext(new ConfirmationStore());
  setContext("figures", new Set<string>());

  const isPending = $derived(
    navigating.to != null && navigating.to.url.pathname !== page.url.pathname,
  );

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
      <div class="grid grid-rows-[auto_1fr_auto] min-block-dvh">
        <a
          href="#content"
          class="fixed inset-s-3 inset-bs-3 z-50 -translate-y-24 rounded-sm border border-foreground bg-card px-3 py-2 text-sm font-semibold opacity-0 focus-visible:translate-y-0 focus-visible:opacity-100"
          >{m.skip_to_content()}</a
        >
        <Header />
        <div
          id="content"
          tabindex="-1"
          class={cn(
            "mx-auto transition-opacity duration-200 outline-none inline-full max-inline-7xl motion-reduce:transition-none",
            { "opacity-55 delay-150": isPending },
          )}
          aria-busy={isPending}
        >
          {@render children()}
        </div>
        <footer class="border-bs">
          <div
            class="mx-auto flex items-center gap-3 px-4 py-5 text-sm text-muted-foreground max-inline-7xl max-sm:px-3"
          >
            <a
              href="https://www.firatciftci.com"
              class="group flex items-center gap-3 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
            >
              <Shahmaran
                class="shrink-0 rotate-20 opacity-30 transition-[rotate,opacity] duration-700 inline-9 group-hover:rotate-[8deg] group-hover:opacity-70 group-focus-visible:rotate-[8deg] group-focus-visible:opacity-70 motion-reduce:transition-none"
              />
              <span
                >{m.footer_credit()}
                <span
                  class="font-semibold text-foreground underline decoration-border underline-offset-4 group-hover:decoration-current"
                  >Firat Ciftci</span
                ></span
              >
            </a>
          </div>
        </footer>
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
  <ProgressBar class="text-marker" displayThresholdMs={300} />
  <ConfirmationDialog />
  <Provenance />
  <Explain />
</QueryClientProvider>
