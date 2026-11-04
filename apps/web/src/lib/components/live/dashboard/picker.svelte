<script lang="ts">
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { on } from "svelte/events";
  import { prefersReducedMotion } from "svelte/motion";
  import { fly } from "svelte/transition";
  import type { Turn } from "#lib/live/ballot.js";
  import type { Summary } from "#lib/live/summary.js";
  import Card from "#lib/components/live/state/card.svelte";
  import Peek from "#lib/components/live/state/peek.svelte";
  import Sheet from "#lib/components/live/state/sheet.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { percent } from "#lib/format.js";
  import { stamp, surname, tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    codes,
    labels,
    senate,
    governor,
    counted,
    closes,
    describe,
    href,
  }: {
    codes: Array<string>;
    labels: Record<string, string>;
    senate: (code: string) => Array<Summary>;
    governor: (code: string) => Array<Summary>;
    counted: (code: string) => number | null;
    closes: (summary: Summary) => number;
    describe: (summary: Summary) => {
      held: { short: string; color: string } | null;
      flip: Turn | null;
    };
    href: (code: string) => string;
  } = $props();

  let picked = $state<string | null>(null);
  let isSheet = $state(false);
  let direction = $state(0);
  let scrub = $state<{ code: string | null; x: number; y: number } | null>(
    null,
  );
  let loupeWidth = $state(0);
  let loupeHeight = $state(0);
  let innerWidth = $state(0);

  const TILES: Array<[string, number, number]> = [
    ["ak", 0, 0],
    ["me", 10, 0],
    ["vt", 9, 1],
    ["nh", 10, 1],
    ["wa", 0, 2],
    ["id", 1, 2],
    ["mt", 2, 2],
    ["nd", 3, 2],
    ["mn", 4, 2],
    ["il", 5, 2],
    ["wi", 6, 2],
    ["mi", 7, 2],
    ["ny", 8, 2],
    ["ri", 9, 2],
    ["ma", 10, 2],
    ["or", 0, 3],
    ["nv", 1, 3],
    ["wy", 2, 3],
    ["sd", 3, 3],
    ["ia", 4, 3],
    ["in", 5, 3],
    ["oh", 6, 3],
    ["pa", 7, 3],
    ["nj", 8, 3],
    ["ct", 9, 3],
    ["ca", 0, 4],
    ["ut", 1, 4],
    ["co", 2, 4],
    ["ne", 3, 4],
    ["mo", 4, 4],
    ["ky", 5, 4],
    ["wv", 6, 4],
    ["va", 7, 4],
    ["md", 8, 4],
    ["de", 9, 4],
    ["az", 1, 5],
    ["nm", 2, 5],
    ["ks", 3, 5],
    ["ar", 4, 5],
    ["tn", 5, 5],
    ["nc", 6, 5],
    ["sc", 7, 5],
    ["dc", 8, 5],
    ["ok", 3, 6],
    ["la", 4, 6],
    ["ms", 5, 6],
    ["al", 6, 6],
    ["ga", 7, 6],
    ["hi", 0, 7],
    ["tx", 3, 7],
    ["fl", 8, 7],
    ["mp", 0, 8],
    ["gu", 1, 8],
    ["as", 2, 8],
    ["pr", 9, 8],
    ["vi", 10, 8],
  ];

  const KEY = [
    { label: m.night_pick_key_held(), kind: "held" },
    { label: m.night_pick_key_ahead(), kind: "ahead" },
    { label: m.night_state_won(), kind: "won" },
    { label: m.night_rating_projected(), kind: "projected" },
  ];

  const known = $derived(new Set(codes));
  const order = $derived(
    TILES.map(([code]) => code).filter(
      (code) =>
        known.has(code) &&
        (senate(code).length > 0 || governor(code).length > 0),
    ),
  );
  const name = (code: string) => labels[code] ?? code.toUpperCase();

  function note(code: string) {
    const all = [...senate(code), ...governor(code)];
    const first = all[0];
    if (first == null) {
      return null;
    }
    if (all.some((summary) => summary.total > 0)) {
      const share = all.find((summary) => summary.counted != null)?.counted;
      return share == null
        ? null
        : m.night_tip_counted({ share: percent(share, 0) });
    }
    const at = closes(first);
    return Date.now() < at
      ? m.night_head_before({ time: stamp(at) })
      : m.night_head_waiting();
  }

  function open(code: string) {
    direction = 0;
    picked = code;
    isSheet = true;
  }

  function neighbor(code: string, by: number) {
    const index = order.indexOf(code);
    return order[(index + by + order.length) % order.length] ?? code;
  }

  function step(by: number) {
    if (picked != null) {
      direction = by;
      picked = neighbor(picked, by);
    }
  }

  function swiping(node: HTMLElement) {
    let start: { x: number; y: number } | null = null;
    const begin = (event: TouchEvent) => {
      const touch = event.touches[0];
      start =
        touch == null || event.touches.length > 1
          ? null
          : { x: touch.clientX, y: touch.clientY };
    };
    const end = (event: TouchEvent) => {
      const touch = event.changedTouches[0];
      if (start == null || touch == null) {
        return;
      }
      const dx = touch.clientX - start.x;
      const dy = touch.clientY - start.y;
      start = null;
      if (Math.abs(dx) > 48 && Math.abs(dx) > Math.abs(dy) * 1.5) {
        step(dx < 0 ? 1 : -1);
      }
    };
    const offs = [
      on(node, "touchstart", begin, { passive: true }),
      on(node, "touchend", end, { passive: true }),
    ];
    return () => {
      for (const off of offs) {
        off();
      }
    };
  }

  function scrubbing(node: HTMLElement) {
    let timer = 0;
    let start: { x: number; y: number } | null = null;
    let isActive = false;
    const at = (x: number, y: number) =>
      document.elementFromPoint(x, y)?.closest<HTMLElement>("[data-code]")
        ?.dataset.code ?? null;
    const reset = () => {
      window.clearTimeout(timer);
      start = null;
      isActive = false;
      scrub = null;
    };
    const begin = (event: TouchEvent) => {
      const touch = event.touches[0];
      if (touch == null || event.touches.length > 1) {
        reset();
        return;
      }
      const point = { x: touch.clientX, y: touch.clientY };
      start = point;
      timer = window.setTimeout(() => {
        isActive = true;
        scrub = { code: at(point.x, point.y), ...point };
      }, 220);
    };
    const move = (event: TouchEvent) => {
      const touch = event.touches[0];
      if (touch == null) {
        return;
      }
      if (!isActive) {
        if (
          start != null &&
          Math.hypot(touch.clientX - start.x, touch.clientY - start.y) > 8
        ) {
          window.clearTimeout(timer);
          start = null;
        }
        return;
      }
      event.preventDefault();
      scrub = {
        code: at(touch.clientX, touch.clientY),
        x: touch.clientX,
        y: touch.clientY,
      };
    };
    const end = (event: TouchEvent) => {
      const code = isActive ? (scrub?.code ?? null) : null;
      if (isActive) {
        event.preventDefault();
      }
      reset();
      if (code != null) {
        open(code);
      }
    };
    const menu = (event: Event) => {
      if (isActive || start != null) {
        event.preventDefault();
      }
    };
    const offs = [
      on(node, "touchstart", begin, { passive: true }),
      on(node, "touchmove", move, { passive: false }),
      on(node, "touchend", end, { passive: false }),
      on(node, "touchcancel", reset),
      on(node, "contextmenu", menu),
    ];
    return () => {
      reset();
      for (const off of offs) {
        off();
      }
    };
  }

  function entries(code: string) {
    const seats = senate(code);
    return [
      ...seats.map((summary) => ({
        summary,
        heading:
          seats.length > 1 ? summary.race.title : m.night_pick_senate_title(),
        letter: m.night_pick_senate(),
      })),
      ...governor(code).map((summary) => ({
        summary,
        heading: m.night_pick_governor_title(),
        letter: m.night_pick_governor(),
      })),
    ];
  }

  function brief(summary: Summary) {
    const ahead =
      summary.winner ??
      summary.projected ??
      (summary.total > 0 ? summary.leader : null);
    if (ahead == null) {
      const held = describe(summary).held;
      return {
        text:
          held == null
            ? m.night_state_open_seat()
            : m.night_state_held_seat({ party: held.short }),
        share: null,
      };
    }
    const index = summary.race.options.findIndex(
      (option) => option.id === ahead.id,
    );
    return {
      text: surname(ahead),
      share:
        summary.total > 0 ? (summary.totals[index] ?? 0) / summary.total : null,
    };
  }

  function look(summary: Summary) {
    const called = summary.winner ?? summary.projected;
    const ahead = called ?? (summary.total > 0 ? summary.leader : null);
    if (ahead == null) {
      const hue = describe(summary).held?.color ?? "var(--muted-foreground)";
      return {
        border: `color-mix(in oklab, ${hue} 70%, transparent)`,
        background: `repeating-linear-gradient(135deg, color-mix(in oklab, ${hue} 35%, transparent) 0 1.5px, transparent 1.5px 4px)`,
        ink: `color-mix(in oklab, ${hue} 60%, var(--foreground))`,
      };
    }
    const hue = tone(ahead);
    if (called == null) {
      return {
        border: hue,
        background: `color-mix(in oklab, ${hue} 32%, var(--card))`,
        ink: `color-mix(in oklab, ${hue} 55%, var(--foreground))`,
      };
    }
    return {
      border: hue,
      background: hue,
      ink: `oklch(from ${hue} clamp(0, (0.7 - l) * 100, 1) 0 0)`,
    };
  }
</script>

{#snippet races(code: string)}
  {const found = $derived(entries(code))}
  {#if found.length === 0}
    <p class="text-sm text-pretty text-muted-foreground">
      {m.night_pick_quiet()}
    </p>
  {:else}
    <div class="grid grid-cols-[minmax(0,1fr)] gap-2.5 divide-y divide-border">
      {#each found as item (item.summary.race.id)}
        {const described = $derived(describe(item.summary))}
        <div class="pbe-2.5 last:pbe-0">
          <Card
            summary={item.summary}
            heading={item.heading}
            held={described.held}
            flip={described.flip}
          />
        </div>
      {/each}
    </div>
  {/if}
{/snippet}

{#snippet swatch(
  summary: Summary | undefined,
  letter: string,
  className: string,
)}
  {#if summary == null}
    <span class={cn("rounded-md", className)} aria-hidden="true"></span>
  {:else}
    {const style = $derived(look(summary))}
    <span
      class={cn(
        "relative grid place-items-center rounded-md border font-bold",
        className,
      )}
      style:border-color={style.border}
      style:background={style.background}
      style:color={style.ink}
      aria-hidden="true"
      >{letter}{#if summary.projected != null}<span
          class="absolute inset-e-px inset-bs-px rounded-full block-0.75 inline-0.75 sm:inset-e-0.5 sm:inset-bs-0.5 sm:block-1 sm:inline-1"
          style:background-color={style.ink}
        ></span>{/if}</span
    >
  {/if}
{/snippet}

<svelte:window bind:innerWidth />

<div class="grid content-start gap-4">
  <ol
    class="grid grid-cols-11 gap-0.5 select-none [-webkit-touch-callout:none] sm:gap-1"
    aria-label={m.night_pick_label()}
    {@attach scrubbing}
  >
    {#each TILES as [code, x, y] (code)}
      {const isKnown = $derived(known.has(code))}
      {const share = $derived(isKnown ? counted(code) : null)}
      {const top = $derived(isKnown ? senate(code)[0] : undefined)}
      {const bottom = $derived(isKnown ? governor(code)[0] : undefined)}
      <li
        class="aspect-square min-inline-0"
        style:grid-column-start={x + 1}
        style:grid-row-start={y + 1}
        data-code={isKnown ? code : undefined}
      >
        {#if isKnown}
          <Peek
            href={href(code)}
            title={name(code)}
            class={cn(
              "relative grid grid-rows-[auto_minmax(0,1fr)] gap-0.5 overflow-hidden rounded-lg border border-border bg-card p-0.5 outline-none block-full inline-full hover:border-foreground focus-visible:border-foreground focus-visible:ring-2 focus-visible:ring-ring/40 data-[state=open]:border-foreground sm:gap-1 sm:p-1",
              {
                "border-foreground ring-2 ring-foreground/40":
                  scrub?.code === code,
              },
            )}
            onTouch={() => open(code)}
          >
            {#snippet card()}
              {const status = $derived(note(code))}
              <span
                class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5"
              >
                <span class="text-sm font-semibold">{name(code)}</span>
                {#if status != null}
                  <span
                    class="font-figures text-xs text-muted-foreground tabular-nums"
                    >{status}</span
                  >
                {/if}
              </span>
              {@render races(code)}
            {/snippet}
            <span
              class={cn(
                "text-[0.625rem]/none font-bold tracking-tight uppercase sm:text-xs/none",
                { "text-muted-foreground": top == null && bottom == null },
              )}>{code}</span
            >
            {#if top != null || bottom != null}
              <span class="grid grid-cols-2 gap-px sm:gap-0.5">
                {@render swatch(
                  top,
                  m.night_pick_senate(),
                  "text-[0.5rem]/none sm:text-[0.6875rem]/none",
                )}
                {@render swatch(
                  bottom,
                  m.night_pick_governor(),
                  "text-[0.5rem]/none sm:text-[0.6875rem]/none",
                )}
              </span>
            {/if}
            {#if share != null && share < 1}
              <span
                class="absolute inset-s-0 inset-bs-0 bg-foreground block-0.5"
                style:inline-size="{share * 100}%"
                aria-hidden="true"
              ></span>
              <span class="sr-only"
                >{m.night_tip_counted({ share: percent(share, 0) })}</span
              >
            {/if}
          </Peek>
        {:else}
          <span
            class="flex rounded-lg border border-dashed border-border p-0.5 text-[0.625rem]/none font-semibold text-muted-foreground uppercase block-full inline-full sm:p-1 sm:text-xs/none"
            aria-hidden="true">{code}</span
          >
        {/if}
      </li>
    {/each}
  </ol>
  <div
    class="grid gap-2.5 text-xs text-muted-foreground"
    role="group"
    aria-label={m.night_pick_key_label()}
  >
    <span class="flex items-center gap-2">
      <span class="flex gap-0.5" aria-hidden="true">
        <span
          class="grid place-items-center rounded-md border border-foreground/50 text-[0.625rem]/none font-bold block-4 inline-4.5"
          >{m.night_pick_senate()}</span
        >
        <span
          class="grid place-items-center rounded-md border border-foreground/50 text-[0.625rem]/none font-bold block-4 inline-4.5"
          >{m.night_pick_governor()}</span
        >
      </span>
      {m.night_pick_key_sides()}
    </span>
    <span class="flex flex-wrap items-center gap-x-4 gap-y-2">
      {#each KEY as item (item.kind)}
        <span class="flex items-center gap-1.5">
          <span
            class={cn("relative rounded-md border block-3.5 inline-4", {
              "border-muted-foreground bg-[repeating-linear-gradient(135deg,color-mix(in_oklab,var(--muted-foreground)_55%,transparent)_0_1.5px,transparent_1.5px_4px)]":
                item.kind === "held",
              "border-foreground bg-[color-mix(in_oklab,var(--foreground)_22%,var(--card))]":
                item.kind === "ahead",
              "border-foreground bg-foreground":
                item.kind === "won" || item.kind === "projected",
            })}
            aria-hidden="true"
          >
            {#if item.kind === "projected"}
              <span
                class="absolute inset-s-1/2 inset-bs-1/2 -translate-1/2 rounded-full bg-background block-1.5 inline-1.5"
              ></span>
            {/if}
          </span>
          {item.label}
        </span>
      {/each}
    </span>
    <span class="text-pretty sm:hidden">{m.night_pick_scrub()}</span>
  </div>
</div>

{#if scrub?.code != null}
  {const code = $derived(scrub.code ?? "")}
  {const status = $derived(note(code))}
  {const left = $derived(
    Math.max(
      8,
      Math.min(scrub.x - loupeWidth / 2, innerWidth - loupeWidth - 8),
    ),
  )}
  {const above = $derived(scrub.y - loupeHeight - 44)}
  <div
    class="pointer-events-none fixed z-50 grid gap-1.5 rounded-md border border-border bg-popover p-2.5 text-popover-foreground shadow-md inline-[min(15rem,calc(100vw-1rem))]"
    style:left="{left}px"
    style:top="{above < 8 ? scrub.y + 44 : above}px"
    bind:clientWidth={loupeWidth}
    bind:clientHeight={loupeHeight}
    aria-hidden="true"
  >
    <span class="flex items-baseline justify-between gap-3">
      <span class="text-sm font-semibold">{name(code)}</span>
      {#if status != null}
        <span class="font-figures text-xs text-muted-foreground tabular-nums"
          >{status}</span
        >
      {/if}
    </span>
    {#each entries(code) as item (item.summary.race.id)}
      {const line = $derived(brief(item.summary))}
      <span class="flex items-center gap-2 text-[0.8125rem]/snug">
        {@render swatch(
          item.summary,
          item.letter,
          "shrink-0 text-[0.625rem]/none block-4 inline-4.5",
        )}
        <span class="truncate">{line.text}</span>
        {#if line.share != null}
          <span class="ms-auto font-figures font-semibold tabular-nums"
            >{percent(line.share)}</span
          >
        {/if}
      </span>
    {:else}
      <span class="text-xs text-muted-foreground">{m.night_pick_quiet()}</span>
    {/each}
  </div>
{/if}

{#if picked != null}
  {const code = $derived(picked)}
  {const previous = $derived(neighbor(code, -1))}
  {const next = $derived(neighbor(code, 1))}
  <Sheet
    bind:isOpen={isSheet}
    title={name(code)}
    href={href(code)}
    action={m.night_pick_open({ state: name(code) })}
  >
    <div class="grid touch-pan-y gap-3" {@attach swiping}>
      {#if order.length > 1}
        <div class="-mbs-2 flex items-center justify-between gap-2">
          <Button
            variant="ghost"
            size="sm"
            class="-ms-2.5 text-muted-foreground"
            aria-label={m.night_pick_previous({ state: name(previous) })}
            onclick={() => step(-1)}
          >
            <ChevronLeftIcon />
            {name(previous)}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            class="-me-2.5 text-muted-foreground"
            aria-label={m.night_pick_next({ state: name(next) })}
            onclick={() => step(1)}
          >
            {name(next)}
            <ChevronRightIcon />
          </Button>
        </div>
      {/if}
      {#key code}
        {const status = $derived(note(code))}
        <div
          class="grid gap-3"
          in:fly={{
            x: direction * 32,
            duration: prefersReducedMotion.current || direction === 0 ? 0 : 180,
          }}
        >
          {#if status != null}
            <p class="font-figures text-xs text-muted-foreground tabular-nums">
              {status}
            </p>
          {/if}
          {@render races(code)}
        </div>
      {/key}
    </div>
  </Sheet>
{/if}
