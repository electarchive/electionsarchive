<script lang="ts">
  // eslint-disable-next-line no-restricted-imports
  import "../app.css";
  import type { Component, Snippet } from "svelte";
  import { onNavigate } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { navigating, page } from "$app/state";
  import { ProgressBar } from "@prgm/sveltekit-progress-bar";
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import { SvelteQueryDevtools } from "@tanstack/svelte-query-devtools";
  import { ModeWatcher, setMode } from "mode-watcher";
  import { setContext } from "svelte";
  import { replay } from "svelte-islands";
  import { on } from "svelte/events";
  import Footer from "#lib/components/footer.svelte";
  import Header from "#lib/components/header.svelte";
  import LanguageBanner from "#lib/components/language-banner.svelte";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import embedStyles from "#lib/embed.css?url";
  import { idle } from "#lib/idle.js";
  import { palette } from "#lib/palette.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { makeQueryClient } from "#lib/query-client.js";
  import {
    ConfirmationStore,
    setConfirmationContext,
  } from "#lib/stores/confirmation.svelte.js";
  import { TEXT_PAGES } from "#lib/text-pages.js";
  import { cn } from "#lib/utils.js";
  import { localizeZone } from "#lib/zone.svelte.js";
  import type { LayoutData } from "./$types";

  const { children, data }: { children: Snippet; data: LayoutData } = $props();

  const queryClient = makeQueryClient();

  setConfirmationContext(new ConfirmationStore());
  setContext("figures", new Set<string>());

  $effect(localizeZone);

  $effect(() => {
    const early = window.takeEarlyClick();
    if (early?.target.isConnected === true) {
      replay(early.target, early.event);
    }
  });

  let Overlays = $state<Component | null>(null);
  let isInteractive = $state(false);

  $effect(() => {
    const offs = (["pointerdown", "keydown"] as const).map((type) =>
      on(window, type, () => (isInteractive = true), { once: true }),
    );
    return () => {
      for (const off of offs) {
        off();
      }
    };
  });

  $effect(() =>
    idle(() => {
      void import("#lib/components/overlays.svelte")
        .then((module) => {
          Overlays = module.default;
        })
        .catch(() => {});
    }),
  );

  const isPending = $derived(
    navigating.to != null &&
      navigating.to.url.pathname !== page.url.pathname &&
      navigating.to.route.id !== "/live/map/[...path]",
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
  {#if TEXT_PAGES[page.url.pathname] == null && !["/", "/[...path]", "/live/[...path]", "/live/map/[...path]", "/people/[slug]", "/parties/[...path]"].includes(page.route.id ?? "")}
    <meta
      name="description"
      content="Every election in ten polities, down to the finest reporting unit, with a source for every number."
    />
  {/if}
  {#if embed != null}
    <base target="_blank" />
    <meta name="robots" content="noindex" />
    <link rel="stylesheet" href={embedStyles} />
  {/if}
</svelte:head>

<QueryClientProvider client={queryClient}>
  <SvelteQueryDevtools />
  <ModeWatcher
    modeStorageKey={embed == null ? undefined : "ea-embed-mode"}
    disableTransitions={isInteractive}
  />
  <Tooltip.Provider>
    {#if embed == null}
      <div class="grid grid-rows-[auto_1fr_auto] min-block-dvh">
        <a
          href="#content"
          class="fixed inset-s-3 inset-bs-3 z-50 -translate-y-24 rounded-sm border border-foreground bg-card px-3 py-2 text-sm font-semibold opacity-0 focus-visible:translate-y-0 focus-visible:opacity-100"
          >{m.skip_to_content()}</a
        >
        <div>
          <noscript>
            <p
              class="hidden border-be border-border bg-card px-3.5 py-2 text-[0.8125rem] [body:has(.island-shell,[data-needs-script])_&]:block"
            >
              {m.noscript_notice()}
              <a
                class="font-semibold underline"
                href={resolve("/[...path]", {
                  path: `${page.url.pathname.slice(1)}.md`,
                })}>{m.noscript_twin()}</a
              >
            </p>
          </noscript>
          <LanguageBanner />
          <Header />
        </div>
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
        <Footer site={data.site} />
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
  {#if Overlays != null}
    <Overlays />
  {/if}
</QueryClientProvider>
