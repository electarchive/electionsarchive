<script lang="ts">
  import { resolve } from "$app/paths";
  import type { atlas } from "#lib/server/world.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const { map }: { map: ReturnType<typeof atlas> } = $props();

  type View = "world" | "europe";
  type Place = (typeof map.world.covered)[number];

  let active = $state<{ key: string; view: View; isPinned: boolean } | null>(
    null,
  );
  let isUnion = $state(false);
  let pointer = "mouse";
  const fade = $props.id();

  const GAP = 110;
  const SPLIT = 70;
  const ASIDE = 0.76;

  const wide = $derived.by(() => {
    const world = (map.world.width / map.world.height) * 1000;
    const europe = (map.europe.width / map.europe.height) * 1000;
    const total = world + GAP + europe;
    const k = 1000 / map.world.height;
    const [, tr, br] = map.world.corners;
    return {
      total,
      columns: `${(world / total) * 100}% ${(GAP / total) * 100}% ${(europe / total) * 100}%`,
      lines: [
        { x1: (tr?.x ?? 0) * k, y1: (tr?.y ?? 0) * k, x2: world + GAP, y2: 0 },
        {
          x1: (br?.x ?? 0) * k,
          y1: (br?.y ?? 0) * k,
          x2: world + GAP,
          y2: 1000,
        },
      ],
    };
  });

  function stacked(share: number) {
    const k = 1000 / map.world.width;
    const top = map.world.height * k + SPLIT;
    const width = share * 1000;
    const left = (1000 - width) / 2;
    const [, , br, bl] = map.world.corners;
    return {
      height: top + (width * map.europe.height) / map.europe.width,
      lines: [
        { x1: (bl?.x ?? 0) * k, y1: (bl?.y ?? 0) * k, x2: left, y2: top },
        {
          x1: (br?.x ?? 0) * k,
          y1: (br?.y ?? 0) * k,
          x2: left + width,
          y2: top,
        },
      ],
    };
  }

  const narrow = $derived(stacked(1));
  const middle = $derived(stacked(ASIDE));
  const shown = $derived.by(() => {
    if (active == null) {
      return null;
    }
    const { key, view } = active;
    const plane = view === "world" ? map.world : map.europe;
    const place = plane.covered.find((entry) => entry.key === key);
    return place == null
      ? null
      : {
          ...place,
          view,
          left: (place.x / plane.width) * 100,
          top: (place.y / plane.height) * 100,
        };
  });

  function enter(event: PointerEvent, place: Place, view: View) {
    pointer = event.pointerType;
    if (event.pointerType === "mouse" || event.pointerType === "pen") {
      active = { key: place.key, view, isPinned: false };
    }
  }

  function leave(event: PointerEvent) {
    if (
      (event.pointerType === "mouse" || event.pointerType === "pen") &&
      active?.isPinned !== true
    ) {
      active = null;
    }
  }

  function tap(event: MouseEvent, place: Place, view: View) {
    if (pointer !== "touch") {
      return;
    }
    if (active?.key !== place.key || active.view !== view || !active.isPinned) {
      event.preventDefault();
      active = { key: place.key, view, isPinned: true };
    }
  }

  function clear(event: PointerEvent) {
    if (
      event.target instanceof Element &&
      event.target.closest("a") == null &&
      active?.isPinned === true
    ) {
      active = null;
    }
  }
</script>

{#snippet lines(
  box: string,
  segments: Array<{ x1: number; y1: number; x2: number; y2: number }>,
  layout: "narrow" | "middle" | "wide",
)}
  <svg
    class={cn("pointer-events-none absolute inset-0 overflow-visible", {
      "sm:hidden": layout === "narrow",
      "lg:hidden max-sm:hidden": layout === "middle",
      "max-lg:hidden": layout === "wide",
    })}
    viewBox={box}
    preserveAspectRatio="none"
    aria-hidden="true"
  >
    {#each segments as segment, index (index)}
      <line
        {...segment}
        class="stroke-foreground/45"
        stroke-width="0.75"
        vector-effect="non-scaling-stroke"
      />
    {/each}
  </svg>
{/snippet}

{#snippet label(view: View)}
  {#if shown != null && shown.view === view}
    <span
      class={cn(
        "pointer-events-none absolute z-10 grid -translate-x-1/2 justify-items-center rounded-xs bg-foreground px-2 py-1 text-center leading-tight whitespace-nowrap text-background shadow-menu",
        {
          "-translate-y-[calc(100%+0.5rem)]": shown.top > 18,
          "translate-y-2": shown.top <= 18,
        },
      )}
      style:inset-inline-start="{Math.min(88, Math.max(12, shown.left))}%"
      style:inset-block-start="{shown.top}%"
      aria-hidden="true"
    >
      <span class="text-sm font-bold">{shown.name}</span>
      <span class="font-figures text-[0.6875rem] tabular-nums opacity-75"
        >{active?.isPinned === true
          ? m.home_map_tap()
          : m.home_since({ year: String(shown.first) })}</span
      >
    </span>
  {/if}
{/snippet}

{#snippet places(
  plane: typeof map.world | typeof map.europe,
  view: View,
  dot: number,
  reach: number,
)}
  <path
    d={plane.rest}
    mask={view === "europe" ? `url(#${fade})` : undefined}
    class="fill-[color-mix(in_oklab,var(--foreground)_6%,var(--background))] stroke-[color-mix(in_oklab,var(--foreground)_38%,var(--background))]"
    stroke-width="0.5"
    stroke-linejoin="round"
    vector-effect="non-scaling-stroke"
  />
  {#each plane.covered as place (place.key)}
    {const isOn = $derived(
      active?.key === place.key || (isUnion && place.isMember),
    )}
    <a
      class="group outline-none"
      href={resolve("/[...path]", { path: place.path })}
      aria-label={place.name}
      tabindex={place.isAside ? -1 : undefined}
      aria-hidden={place.isAside ? "true" : undefined}
      data-active={isOn ? "" : undefined}
      onpointerenter={(event) => enter(event, place, view)}
      onpointerleave={leave}
      onpointerdown={(event) => (pointer = event.pointerType)}
      onfocus={() => (active = { key: place.key, view, isPinned: false })}
      onblur={() => (active = null)}
      onclick={(event) => tap(event, place, view)}
    >
      <path
        d={place.d}
        class="fill-[color-mix(in_oklab,var(--foreground)_84%,var(--background))] stroke-background transition-[fill] duration-100 group-hover:fill-marker group-focus-visible:fill-marker group-focus-visible:stroke-foreground group-data-active:fill-marker"
        stroke-width="0.6"
        vector-effect="non-scaling-stroke"
      />
      {#if place.isTiny}
        <circle
          cx={place.x}
          cy={place.y}
          r={dot}
          class="fill-[color-mix(in_oklab,var(--foreground)_84%,var(--background))] stroke-background group-hover:fill-marker group-focus-visible:fill-marker group-focus-visible:stroke-foreground group-data-active:fill-marker"
          stroke-width="1"
          vector-effect="non-scaling-stroke"
        />
      {/if}
    </a>
  {/each}
  {#each plane.covered.filter((place) => place.isTiny) as place (place.key)}
    <a
      class="outline-none"
      href={resolve("/[...path]", { path: place.path })}
      tabindex="-1"
      aria-hidden="true"
      onpointerenter={(event) => enter(event, place, view)}
      onpointerleave={leave}
      onpointerdown={(event) => (pointer = event.pointerType)}
      onclick={(event) => tap(event, place, view)}
    >
      <circle cx={place.x} cy={place.y} r={reach} class="fill-transparent" />
    </a>
  {/each}
{/snippet}

<div class="grid gap-4">
  <div
    class="relative lg:grid lg:grid-cols-(--columns) lg:items-start"
    style:--columns={wide.columns}
    role="presentation"
    onpointerdown={clear}
  >
    <div class="relative">
      <svg
        class="block block-auto inline-full"
        viewBox="0 0 {map.world.width} {map.world.height}"
        role="group"
        aria-label={m.home_map_world_label()}
      >
        {@render places(map.world, "world", 3, 9)}
        <path
          d={map.world.frame}
          class="pointer-events-none fill-none stroke-foreground"
          stroke-width="1"
          vector-effect="non-scaling-stroke"
        />
      </svg>
      {@render label("world")}
    </div>
    <div
      class="relative mbs-[7%] outline-1 -outline-offset-1 outline-foreground lg:col-start-3 lg:mbs-0 max-lg:sm:mx-auto max-lg:sm:inline-[76%]"
    >
      <svg
        class="block block-auto inline-full"
        viewBox="0 0 {map.europe.width} {map.europe.height}"
        role="group"
        aria-label={m.home_map_europe_label()}
      >
        <defs>
          <linearGradient id="{fade}-x">
            <stop offset="0" stop-color="black" />
            <stop offset="0.07" stop-color="white" />
            <stop offset="0.93" stop-color="white" />
            <stop offset="1" stop-color="black" />
          </linearGradient>
          <linearGradient id="{fade}-y" x2="0" y2="1">
            <stop offset="0" stop-color="black" />
            <stop offset="0.07" stop-color="white" />
            <stop offset="0.93" stop-color="white" />
            <stop offset="1" stop-color="black" />
          </linearGradient>
          <mask id="{fade}-edge" maskUnits="userSpaceOnUse">
            <rect
              width={map.europe.width}
              height={map.europe.height}
              fill="url(#{fade}-y)"
            />
          </mask>
          <mask id={fade} maskUnits="userSpaceOnUse">
            <rect
              width={map.europe.width}
              height={map.europe.height}
              fill="url(#{fade}-x)"
              mask="url(#{fade}-edge)"
            />
          </mask>
        </defs>
        {@render places(map.europe, "europe", 7, 22)}
        <g
          class="pointer-events-none fill-none"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path
            d={map.europe.border}
            class="stroke-background"
            stroke-width="4.5"
            vector-effect="non-scaling-stroke"
          />
          <path
            d={map.europe.border}
            class="stroke-foreground"
            stroke-width="1.5"
            vector-effect="non-scaling-stroke"
          />
        </g>
      </svg>
      <span
        class="pointer-events-none absolute inset-s-0 inset-bs-0 bg-background px-2 py-1 text-xs font-semibold"
        aria-hidden="true">{m.home_map_europe()}</span
      >
      {@render label("europe")}
    </div>
    {@render lines(`0 0 1000 ${narrow.height}`, narrow.lines, "narrow")}
    {@render lines(`0 0 1000 ${middle.height}`, middle.lines, "middle")}
    {@render lines(`0 0 ${wide.total} 1000`, wide.lines, "wide")}
  </div>
  <ul
    class="flex flex-wrap items-center gap-x-5 gap-y-2 text-[0.8125rem] text-muted-foreground"
  >
    <li class="flex items-center gap-2">
      <span
        class="rounded-[1px] bg-[color-mix(in_oklab,var(--foreground)_84%,var(--background))] block-3 inline-4"
        aria-hidden="true"
      ></span>
      <span class="font-semibold text-foreground"
        >{m.home_map_covered({ count: map.count })}</span
      >
    </li>
    <li class="flex items-center gap-2">
      <span
        class="rounded-[1px] border border-[color-mix(in_oklab,var(--foreground)_38%,var(--background))] bg-[color-mix(in_oklab,var(--foreground)_6%,var(--background))] block-3 inline-4"
        aria-hidden="true"
      ></span>
      {m.home_map_missing()}
    </li>
    {#if map.union != null}
      <li class="flex items-center gap-2">
        <svg class="block-3 inline-5" viewBox="0 0 20 12" aria-hidden="true"
          ><rect
            width="10"
            height="12"
            class="fill-[color-mix(in_oklab,var(--foreground)_84%,var(--background))]"
          /><rect
            x="10"
            width="10"
            height="12"
            class="fill-[color-mix(in_oklab,var(--foreground)_6%,var(--background))]"
          /><path
            d="M10 0v12"
            class="stroke-background"
            stroke-width="4"
          /><path
            d="M10 0v12"
            class="stroke-foreground"
            stroke-width="1.5"
          /></svg
        >
        <a
          class="font-semibold text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
          href={resolve("/[...path]", { path: map.union.path })}
          onpointerenter={() => (isUnion = true)}
          onpointerleave={() => (isUnion = false)}
          onfocus={() => (isUnion = true)}
          onblur={() => (isUnion = false)}>{m.home_map_union()}</a
        >
      </li>
    {/if}
  </ul>
</div>
