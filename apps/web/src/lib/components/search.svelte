<script lang="ts">
  import type { Component } from "svelte";
  import { afterNavigate } from "$app/navigation";
  import { page } from "$app/state";
  import SearchIcon from "@lucide/svelte/icons/search";
  import { on } from "svelte/events";
  import { m } from "#lib/paraglide/messages.js";
  import { finder, summon } from "#lib/search.svelte.js";
  import { cn } from "#lib/utils.js";

  const { class: className }: { class?: string } = $props();

  const STORE = "ea:recent";

  let Palette = $state<Component | null>(null);
  let loading: Promise<void> | null = null;

  function load() {
    loading ??= import("./search-palette.svelte")
      .then((module) => {
        Palette = module.default;
      })
      .catch(() => {
        loading = null;
      });
  }

  $effect(() => {
    if (finder.isOpen) {
      load();
    }
  });

  $effect(() => {
    if ("requestIdleCallback" in window) {
      const id = requestIdleCallback(load, { timeout: 5000 });
      return () => cancelIdleCallback(id);
    }
    const timer = setTimeout(load, 3000);
    return () => clearTimeout(timer);
  });

  $effect(() =>
    on(document, "keydown", (event) => {
      if (
        event.key.toLowerCase() === "k" &&
        (event.metaKey || event.ctrlKey) &&
        !event.altKey
      ) {
        event.preventDefault();
        finder.isOpen = !finder.isOpen;
      }
    }),
  );

  afterNavigate(() => {
    const path = page.url.pathname.slice(1);
    if (path === "" || page.status !== 200) {
      return;
    }
    setTimeout(() => {
      const name = document.title.split(" | ")[0]?.trim() ?? "";
      if (name === "") {
        return;
      }
      try {
        const recent = JSON.parse(
          localStorage.getItem(STORE) ?? "[]",
        ) as Array<{ path: string; name: string }>;
        localStorage.setItem(
          STORE,
          JSON.stringify(
            [
              { path, name },
              ...recent.filter((entry) => entry.path !== path),
            ].slice(0, 6),
          ),
        );
      } catch {}
    }, 0);
  });
</script>

<button
  type="button"
  class={cn(
    "group/search flex items-center gap-2.5 rounded-sm border border-input bg-card ps-3 pe-2 text-start text-sm text-muted-foreground transition-colors block-9 min-inline-0 hover:border-foreground/40 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring",
    className,
  )}
  aria-haspopup="dialog"
  onpointerenter={load}
  onfocus={load}
  onclick={() => summon()}
>
  <SearchIcon class="shrink-0 text-foreground block-4 inline-4" />
  <span class="flex-1 truncate min-inline-0 sm:hidden">{m.search_open()}</span>
  <span class="flex-1 truncate min-inline-0 max-sm:hidden"
    >{m.search_page_placeholder()}</span
  >
  <kbd
    class="shrink-0 rounded-xs border border-input px-1 font-figures text-[0.6875rem] keycap max-md:hidden"
    >⌘K</kbd
  >
</button>

{#if Palette != null}
  <Palette />
{/if}
