<script lang="ts">
  import type { Summary } from "#lib/live/summary.js";
  import Card from "#lib/components/live/state/card.svelte";
  import Peek from "#lib/components/live/state/peek.svelte";
  import Sheet from "#lib/components/live/state/sheet.svelte";
  import { percent } from "#lib/format.js";
  import { stamp, tone } from "#lib/live/format.js";
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
      isFlip: boolean;
    };
    href: (code: string) => string;
  } = $props();

  let picked = $state<string | null>(null);
  let isSheet = $state(false);

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
  ];

  const known = $derived(new Set(codes));
  const name = (code: string) => labels[code] ?? code.toUpperCase();

  function note(code: string) {
    const all = [...senate(code), ...governor(code)];
    const first = all[0];
    if (first == null || all.some((summary) => summary.total > 0)) {
      return null;
    }
    const at = closes(first);
    return Date.now() < at
      ? m.night_head_before({ time: stamp(at) })
      : m.night_head_waiting();
  }

  function look(summary: Summary) {
    const called = summary.winner ?? summary.projected;
    const ahead = called ?? (summary.total > 0 ? summary.leader : null);
    if (ahead == null) {
      const hue = describe(summary).held?.color ?? "var(--muted-foreground)";
      return {
        isDashed: true,
        border: `color-mix(in oklab, ${hue} 80%, transparent)`,
        fill: "transparent",
        ink: `color-mix(in oklab, ${hue} 55%, var(--foreground))`,
      };
    }
    const hue = tone(ahead);
    if (called == null) {
      return {
        isDashed: false,
        border: hue,
        fill: `color-mix(in oklab, ${hue} 22%, transparent)`,
        ink: `color-mix(in oklab, ${hue} 45%, var(--foreground))`,
      };
    }
    return {
      isDashed: false,
      border: hue,
      fill: hue,
      ink: `oklch(from ${hue} clamp(0, (0.7 - l) * 100, 1) 0 0)`,
    };
  }
</script>

{#snippet races(code: string)}
  {const found = $derived([
    ...senate(code).map((summary) => ({
      summary,
      heading:
        senate(code).length > 1
          ? summary.race.title
          : m.night_pick_senate_title(),
    })),
    ...governor(code).map((summary) => ({
      summary,
      heading: m.night_pick_governor_title(),
    })),
  ])}
  {#if found.length === 0}
    <p class="text-sm text-pretty text-muted-foreground">
      {m.night_pick_quiet()}
    </p>
  {:else}
    <div class="grid gap-2.5 divide-y divide-border">
      {#each found as item (item.summary.race.id)}
        {const described = $derived(describe(item.summary))}
        <div class="pbe-2.5 last:pbe-0">
          <Card
            summary={item.summary}
            heading={item.heading}
            held={described.held}
            isFlip={described.isFlip}
          />
        </div>
      {/each}
    </div>
  {/if}
{/snippet}

{#snippet mark(summary: Summary | undefined, letter: string)}
  {#if summary != null}
    {const style = $derived(look(summary))}
    <span
      class={cn(
        "relative grid place-items-center rounded-[3px] border text-[0.5625rem]/none font-bold block-3.5 inline-3.5 sm:text-[0.6875rem]/none sm:block-4.5 sm:inline-4.5",
        { "border-dashed": style.isDashed },
      )}
      style:border-color={style.border}
      style:background-color={style.fill}
      style:color={style.ink}
      aria-hidden="true"
      >{letter}{#if summary.projected != null}<span
          class="absolute inset-e-px inset-bs-px rounded-full block-0.75 inline-0.75 sm:inset-e-0.5 sm:inset-bs-0.5 sm:block-1 sm:inline-1"
          style:background-color={style.ink}
        ></span>{/if}</span
    >
  {/if}
{/snippet}

<ol
  class="grid grid-cols-11 gap-0.5 sm:gap-1"
  aria-label={m.night_pick_label()}
>
  {#each TILES as [code, x, y] (code)}
    {const isKnown = $derived(known.has(code))}
    {const share = $derived(isKnown ? counted(code) : null)}
    <li
      class="aspect-square min-inline-0"
      style:grid-column-start={x + 1}
      style:grid-row-start={y + 1}
    >
      {#if isKnown}
        <Peek
          href={href(code)}
          title={name(code)}
          class="relative flex flex-col justify-between overflow-hidden rounded-xs border border-border bg-card p-0.5 outline-none block-full inline-full hover:border-foreground focus-visible:border-foreground focus-visible:ring-2 focus-visible:ring-ring/40 data-[state=open]:border-foreground sm:p-1"
          onTouch={() => {
            picked = code;
            isSheet = true;
          }}
        >
          {#snippet card()}
            {const status = $derived(note(code))}
            <span class="flex items-baseline justify-between gap-3">
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
              "text-[0.625rem]/none font-bold uppercase sm:text-xs/none",
              {
                "text-muted-foreground":
                  senate(code).length === 0 && governor(code).length === 0,
              },
            )}>{code}</span
          >
          <span class="flex gap-px sm:gap-0.5">
            {@render mark(senate(code)[0], m.night_pick_senate())}
            {@render mark(governor(code)[0], m.night_pick_governor())}
          </span>
          {#if share != null}
            <span
              class="absolute inset-s-0 inset-be-0 bg-foreground block-0.5"
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
          class="flex rounded-xs border border-dashed border-border p-0.5 text-[0.625rem]/none font-semibold text-muted-foreground uppercase block-full inline-full sm:p-1 sm:text-xs/none"
          aria-hidden="true">{code}</span
        >
      {/if}
    </li>
  {/each}
</ol>

{#if picked != null}
  {const code = $derived(picked)}
  <Sheet
    bind:isOpen={isSheet}
    title={name(code)}
    href={href(code)}
    action={m.night_pick_open({ state: name(code) })}
  >
    {const status = $derived(note(code))}
    {#if status != null}
      <p class="font-figures text-xs text-muted-foreground tabular-nums">
        {status}
      </p>
    {/if}
    {@render races(code)}
  </Sheet>
{/if}
