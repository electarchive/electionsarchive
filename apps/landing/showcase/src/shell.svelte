<script lang="ts">
  import ArrowLeftIcon from "@lucide/svelte/icons/arrow-left";
  import ArrowRightIcon from "@lucide/svelte/icons/arrow-right";
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import { ModeWatcher } from "mode-watcher";
  import { setContext } from "svelte";
  import { on } from "svelte/events";
  import { fly } from "svelte/transition";
  import Monogram from "#lib/components/monogram.svelte";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import { makeQueryClient } from "#lib/query-client.js";
  import {
    ConfirmationStore,
    setConfirmationContext,
  } from "#lib/stores/confirmation.svelte.js";
  import ArchivePage from "#routes/[...path]/+page.svelte";
  import PartyPage from "#routes/parties/[...path]/+page.svelte";
  import PersonPage from "#routes/people/[slug]/+page.svelte";
  import { pages } from "../pages.js";
  import { notice, show, showcase, slug } from "./shims/router.svelte.js";
  import { page } from "./shims/state.svelte.js";

  const queryClient = makeQueryClient();
  setConfirmationContext(new ConfirmationStore());
  setContext("figures", new Set<string>());

  const at = pages.findIndex((entry) => entry.slug === slug);
  const previous = pages[(at + pages.length - 1) % pages.length];
  const next = pages[(at + 1) % pages.length];
  const kind = $derived(
    page.url.pathname.startsWith("/parties/")
      ? "party"
      : page.url.pathname.startsWith("/people/")
        ? "person"
        : "archive",
  );

  $effect(() =>
    on(
      document,
      "click",
      (event) => {
        const anchor =
          event.target instanceof Element
            ? event.target.closest<HTMLAnchorElement>("a[href]")
            : null;
        if (
          anchor == null ||
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey ||
          anchor.target === "_blank" ||
          anchor.hasAttribute("download")
        ) {
          return;
        }
        const url = new URL(anchor.href);
        if (
          url.origin !== location.origin ||
          url.pathname === "/" ||
          url.pathname.startsWith("/showcase/")
        ) {
          return;
        }
        event.preventDefault();
        void show(url);
      },
      { capture: true },
    ),
  );

  $effect(() => {
    if (notice.text == null) {
      return;
    }
    const stamp = notice.at;
    const timer = setTimeout(() => {
      if (notice.at === stamp) {
        notice.text = null;
      }
    }, 3200);
    return () => clearTimeout(timer);
  });
</script>

<QueryClientProvider client={queryClient}>
  <ModeWatcher />
  <Tooltip.Provider>
    <div class="grid grid-rows-[auto_auto_1fr] min-block-dvh">
      <header class="border-be">
        <div
          class="mx-auto flex items-center justify-between gap-4 px-4 block-12 max-inline-7xl"
        >
          <a href="/" class="flex items-center gap-2.5">
            <Monogram size={26} />
            <span class="text-lg font-[780] tracking-[-0.015em]"
              >{m.wordmark()}</span
            >
          </a>
          <span class="text-sm text-muted-foreground">Static preview</span>
        </div>
      </header>
      <nav class="border-be bg-muted/60" aria-label="Previews">
        <div class="mx-auto flex items-center gap-3 px-4 py-2 max-inline-7xl">
          <a
            href="/showcase/{previous.slug}/"
            class="grid shrink-0 place-items-center rounded-sm border bg-background text-muted-foreground block-8 inline-8 hover:text-foreground"
            aria-label="Previous: {previous.title}"
          >
            <ArrowLeftIcon class="size-4" />
          </a>
          <p class="flex-1 text-sm/snug min-inline-0">
            <span class="font-figures text-muted-foreground tabular-nums"
              >{String(at + 1).padStart(2, "0")}/{pages.length}</span
            >
            <span
              class="ms-1.5 font-semibold"
              style:view-transition-name="showcase-{slug}"
              >{showcase.title}</span
            >
            <span class="text-muted-foreground max-sm:hidden"
              >{showcase.caption}</span
            >
          </p>
          <a
            href="/showcase/{next.slug}/"
            class="grid shrink-0 place-items-center rounded-sm border bg-background text-muted-foreground block-8 inline-8 hover:text-foreground"
            aria-label="Next: {next.title}"
          >
            <ArrowRightIcon class="size-4" />
          </a>
        </div>
      </nav>
      <div class="mx-auto inline-full max-inline-7xl">
        {#if kind === "party"}
          <PartyPage data={page.data as never} />
        {:else if kind === "person"}
          <PersonPage data={page.data as never} />
        {:else}
          <ArchivePage data={page.data as never} />
        {/if}
      </div>
    </div>
  </Tooltip.Provider>
  {#if notice.text != null}
    <p
      class="fixed inset-x-4 bottom-4 z-50 mx-auto rounded-sm bg-primary px-3.5 py-2.5 text-center text-sm text-primary-foreground shadow-lg max-inline-sm"
      role="status"
      transition:fly={{ y: 12, duration: 180 }}
    >
      {notice.text}
    </p>
  {/if}
</QueryClientProvider>
