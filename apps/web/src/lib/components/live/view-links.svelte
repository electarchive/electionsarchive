<script lang="ts">
  import { resolve } from "$app/paths";
  import ArrowRightIcon from "@lucide/svelte/icons/arrow-right";
  import LayoutDashboardIcon from "@lucide/svelte/icons/layout-dashboard";
  import MapIcon from "@lucide/svelte/icons/map";
  import { m } from "#lib/paraglide/messages.js";

  const { election, isNation }: { election: string; isNation: boolean } =
    $props();
</script>

<nav
  class="grid gap-2 sm:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] sm:max-inline-176"
  aria-label={m.live_views_label()}
>
  <a
    href={resolve("/live/map/[...path]", { path: election })}
    class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-sm border border-primary bg-primary p-3 pe-4 text-primary-foreground keycap hover:bg-primary/90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
  >
    <span
      class="grid place-items-center rounded-xs bg-primary-foreground/12 block-11 inline-11"
      aria-hidden="true"
    >
      <MapIcon class="block-5.5 inline-5.5" />
    </span>
    <span class="grid gap-0.5">
      <span class="text-[0.9375rem]/5 font-[760] tracking-tight"
        >{m.livemap_tile_title()}</span
      >
      <span class="text-xs/4 text-pretty text-primary-foreground/72"
        >{isNation ? m.livemap_tile_nation() : m.livemap_tile_state()}</span
      >
    </span>
    <ArrowRightIcon
      class="block-4.5 inline-4.5 rtl:-scale-x-100"
      aria-hidden="true"
    />
  </a>
  <a
    href={resolve("/live/board/[...path]", { path: election })}
    class="group grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-sm border border-input bg-card p-3 pe-4 keycap hover:bg-secondary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
  >
    <span
      class="grid place-items-center rounded-xs bg-secondary block-11 inline-11 group-hover:bg-background"
      aria-hidden="true"
    >
      <LayoutDashboardIcon class="block-5.5 inline-5.5" />
    </span>
    <span class="grid gap-0.5">
      <span class="text-[0.9375rem]/5 font-[760] tracking-tight"
        >{m.board_tile_title()}</span
      >
      <span class="text-xs/4 text-pretty text-muted-foreground"
        >{m.board_tile_body()}</span
      >
    </span>
    <ArrowRightIcon
      class="text-muted-foreground block-4.5 inline-4.5 group-hover:text-foreground rtl:-scale-x-100"
      aria-hidden="true"
    />
  </a>
</nav>
