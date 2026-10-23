<script lang="ts">
  import { resolve } from "$app/paths";
  import Export from "#lib/components/export.svelte";
  import Name from "#lib/components/name.svelte";
  import { count, credit, percent } from "#lib/format.js";
  import { LOST } from "#lib/outcomes.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const { people }: { people: NonNullable<PageData["people"]> } = $props();

  const PAD = { top: 12, right: 14, bottom: 24, left: 34 };
  const OUTCOMES: Record<string, () => string> = {
    elected: m.outcome_elected,
    won: m.outcome_won,
    advanced: m.outcome_advanced,
    tied: m.outcome_tied,
    lost: m.person_lost_mark,
    withdrew: m.outcome_withdrew,
    disqualified: m.outcome_disqualified,
  };

  let width = $state(0);
  let hovered = $state<string | null>(null);

  const sides = $derived(
    people.map((person, side) => {
      const marks = person.record.map((entry) => ({
        ...entry,
        side,
        id: `${side}:${entry.path ?? entry.name}:${entry.date}`,
        at:
          entry.year +
          (Date.parse(`${entry.date}T00:00:00Z`) -
            Date.UTC(Number(entry.date.slice(0, 4)), 0, 1)) /
            31_622_400_000,
        share:
          entry.kind === "indirect" ||
          entry.votes == null ||
          entry.total == null ||
          entry.total === 0
            ? null
            : entry.votes / entry.total,
        isWon:
          entry.outcome === "elected" ||
          entry.outcome === "won" ||
          entry.outcome === "advanced",
        tint: swatch(
          entry.party?.color ?? null,
          entry.party == null ? "" : `party:${entry.party.id}`,
        ),
      }));
      const years = marks.map((mark) => mark.year);
      const elections = [
        ...Map.groupBy(marks, (mark) => mark.election).values(),
      ];
      return {
        ...person,
        marks,
        ran: elections.length,
        won: elections.filter((group) =>
          group.some(
            (mark) => mark.outcome === "elected" || mark.outcome === "won",
          ),
        ).length,
        span:
          years.length === 0
            ? "–"
            : Math.min(...years) === Math.max(...years)
              ? String(Math.min(...years))
              : m.polity_span({
                  first: String(Math.min(...years)),
                  last: String(Math.max(...years)),
                }),
      };
    }),
  );
  const marks = $derived(
    sides.flatMap((side) => side.marks.filter((mark) => mark.share != null)),
  );
  const range = $derived.by(() => {
    const low = Math.min(...marks.map((mark) => mark.at));
    const high = Math.max(...marks.map((mark) => mark.at));
    const pad = Math.max(1, (high - low) * 0.04);
    return { low: low - pad, high: high + pad };
  });
  const top = $derived(
    Math.min(
      1,
      Math.ceil(Math.max(0.5, ...marks.map((mark) => mark.share ?? 0)) * 10) /
        10,
    ),
  );
  const height = $derived(
    Math.round(Math.min(280, Math.max(190, width * 0.3))),
  );
  const sx = (value: number) =>
    PAD.left +
    ((value - range.low) / (range.high - range.low)) *
      (width - PAD.left - PAD.right);
  const sy = (value: number) =>
    PAD.top + ((top - value) / top) * (height - PAD.top - PAD.bottom);
  const decades = $derived.by(() => {
    const step =
      range.high - range.low > 40 ? 10 : range.high - range.low > 16 ? 4 : 2;
    const ticks: Array<number> = [];
    for (
      let year = Math.ceil(range.low / step) * step;
      year <= range.high;
      year += step
    ) {
      ticks.push(year);
    }
    return ticks;
  });
  const shared = $derived(
    (sides[0]?.marks ?? []).flatMap((mark) => {
      const other = sides[1]?.marks.find(
        (entry) => entry.path != null && entry.path === mark.path,
      );
      return other == null || Math.max(mark.share ?? 1, other.share ?? 1) < 0.01
        ? []
        : [{ path: mark.path, pair: [mark, other] }];
    }),
  );
  const focus = $derived(marks.find((mark) => mark.id === hovered) ?? null);
</script>

{#snippet glyph(side: number, isWon: boolean, tint: string, size = 10)}
  <svg
    class="shrink-0"
    width={size}
    height={size}
    viewBox="0 0 10 10"
    aria-hidden="true"
  >
    {#if side === 0}
      <circle
        cx="5"
        cy="5"
        r={isWon ? 4.5 : 3.75}
        fill={isWon ? tint : "var(--card)"}
        stroke={tint}
        stroke-width={isWon ? 0 : 1.5}
      />
    {:else}
      <rect
        x={isWon ? 1 : 1.75}
        y={isWon ? 1 : 1.75}
        width={isWon ? 8 : 6.5}
        height={isWon ? 8 : 6.5}
        fill={isWon ? tint : "var(--card)"}
        stroke={tint}
        stroke-width={isWon ? 0 : 1.5}
      />
    {/if}
  </svg>
{/snippet}

<dl class="col-span-12 grid grid-cols-2 gap-px">
  {#each sides as side, index (side.id)}
    <div
      class={cn("grid content-start gap-2 bg-card px-3.5 py-3", {
        "sm:grid-cols-[auto_minmax(0,1fr)] sm:gap-x-4": side.photo != null,
      })}
    >
      {#if side.photo != null}
        <img
          class="aspect-4/5 rounded-sm bg-muted object-cover object-top ring-1 ring-border inline-14 sm:row-span-2 sm:inline-20"
          src={side.photo.src}
          alt={m.photo_alt({ name: side.name })}
          width="80"
          height="100"
        />
      {/if}
      <dt class="flex items-center gap-2">
        {@render glyph(index, true, "var(--foreground)", 12)}
        <a
          class="truncate text-lg font-[720] hover:highlighter"
          href={resolve("/id/[id]", { id: side.id })}>{side.name}</a
        >
      </dt>
      <dd class="grid gap-2">
        {#if side.party != null}
          <Name
            class="justify-self-start text-[0.8125rem]"
            label={side.party.label}
            color={swatch(side.party.color, `party:${side.party.id}`)}
            party={side.party.id}
          />
        {:else if side.isIndependent}
          <span class="text-[0.8125rem] font-semibold"
            >{m.person_parties_none()}</span
          >
        {/if}
        {#if side.caucus != null}
          <span class="text-[0.8125rem] text-muted-foreground"
            >{m.person_caucus({
              caucus: side.caucus.short ?? side.caucus.label,
            })}</span
          >
        {/if}
        <span class="flex flex-wrap gap-x-4 gap-y-1.5">
          {#each [[m.person_ran(), count(side.ran)], [m.person_won(), count(side.won)], [m.person_years(), side.span]] as [label, value] (label)}
            <span class="grid gap-0.5">
              <span class="text-xs text-muted-foreground">{label}</span>
              <span
                class="font-figures text-xl tracking-[-0.02em] whitespace-nowrap tabular-nums max-sm:text-base"
                >{value}</span
              >
            </span>
          {/each}
        </span>
        {#if side.photo != null}
          <a
            class="justify-self-start text-[0.6875rem] text-muted-foreground underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-foreground"
            href={side.photo.source}
            rel="external noopener">{credit(side.photo)}</a
          >
        {/if}
      </dd>
    </div>
  {/each}
</dl>

{#if marks.length > 0}
  <section
    class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="shares"
    data-export
  >
    <Export name="shares" class="absolute inset-e-2 inset-bs-2" />
    <div class="grid gap-0.5 pe-8">
      <h2 id="shares" class="heading">
        {m.versus_shares_title()}
      </h2>
      <p class="text-[0.8125rem] text-muted-foreground">
        {m.versus_shares_lede()}
      </p>
    </div>
    <p
      class="flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.8125rem] block-5"
    >
      {#if focus == null}
        {#each sides as side, index (side.id)}
          <span class="inline-flex items-center gap-1.5"
            >{@render glyph(index, true, "var(--foreground)")}{side.name}</span
          >
        {/each}
      {:else}
        <span class="inline-flex items-center gap-1.5 truncate">
          {@render glyph(focus.side, focus.isWon, focus.tint)}
          <span class="font-semibold">{sides[focus.side]?.name}</span>
          <span class="truncate text-muted-foreground">{focus.name}</span>
          <span class="font-figures tabular-nums"
            >{percent(focus.share ?? 0)}</span
          >
        </span>
      {/if}
    </p>
    <div
      bind:clientWidth={width}
      class="min-inline-0"
      style:block-size={width === 0 ? "clamp(190px, 30vw, 280px)" : null}
    >
      {#if width > 0}
        <svg
          viewBox="0 0 {width} {height}"
          class="block overflow-visible inline-full"
          style:block-size="{height}px"
          role="img"
          aria-label={m.versus_shares_title()}
          onpointerleave={() => (hovered = null)}
          onpointermove={(event) => {
            const box = event.currentTarget.getBoundingClientRect();
            const [x, y] = [event.clientX - box.left, event.clientY - box.top];
            let best = { id: null as string | null, distance: 24 };
            for (const mark of marks) {
              const distance = Math.hypot(
                sx(mark.at) - x,
                sy(mark.share ?? 0) - y,
              );
              if (distance < best.distance) {
                best = { id: mark.id, distance };
              }
            }
            hovered = best.id;
          }}
        >
          {#each [0, top / 2, top] as tick (tick)}
            <line
              x1={PAD.left}
              x2={width - PAD.right}
              y1={sy(tick)}
              y2={sy(tick)}
              class="stroke-border"
            />
            <text
              x={PAD.left - 6}
              y={sy(tick) + 3.5}
              text-anchor="end"
              class="fill-muted-foreground font-figures text-[0.625rem]"
              >{Math.round(tick * 100)}</text
            >
          {/each}
          {#if top >= 0.5}
            <line
              x1={PAD.left}
              x2={width - PAD.right}
              y1={sy(0.5)}
              y2={sy(0.5)}
              class="stroke-foreground/40"
              stroke-dasharray="3 3"
            />
          {/if}
          {#each decades as year (year)}
            <text
              x={sx(year)}
              y={height - 6}
              text-anchor="middle"
              class="fill-muted-foreground font-figures text-[0.625rem]"
              >{year}</text
            >
          {/each}
          {#each sides as side, index (side.id)}
            <polyline
              points={side.marks
                .filter(
                  (mark) =>
                    mark.share != null &&
                    mark.kind !== "primary" &&
                    mark.kind !== "runoff",
                )
                .toSorted((a, b) => a.at - b.at)
                .map((mark) => `${sx(mark.at)},${sy(mark.share ?? 0)}`)
                .join(" ")}
              fill="none"
              class="stroke-foreground/25"
              stroke-dasharray={index === 0 ? "" : "4 3"}
            />
          {/each}
          {#each marks as mark (mark.id)}
            <g
              class={cn({
                "opacity-45": mark.kind === "primary" && focus !== mark,
              })}
              transform="translate({sx(mark.at) - (focus === mark ? 7 : 5)} {sy(
                mark.share ?? 0,
              ) - (focus === mark ? 7 : 5)})"
            >
              {@render glyph(
                mark.side,
                mark.isWon,
                mark.tint,
                focus === mark ? 14 : 10,
              )}
            </g>
          {/each}
        </svg>
      {/if}
    </div>
  </section>
{/if}

<section
  class="relative col-span-12 grid gap-2 bg-card px-3.5 pbs-3.5 pbe-3"
  aria-labelledby="head"
  data-export
>
  <Export name="head-to-head" class="absolute inset-e-2 inset-bs-2" />
  <div class="grid gap-0.5 pe-8">
    <h2 id="head" class="heading">
      {m.versus_head_title()}
    </h2>
    <p class="text-[0.8125rem] text-muted-foreground">
      {shared.length === 0 ? m.versus_head_none() : m.versus_head_lede()}
    </p>
  </div>
  {#if shared.length > 0}
    <ol class="grid text-sm">
      {#each shared as match (match.path)}
        {const first = $derived(match.pair[0])}
        <li
          class="grid grid-cols-[3rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 border-be border-border/60 py-2 last:border-be-0"
        >
          <span class="font-figures text-[0.8125rem] text-muted-foreground"
            >{first?.year}</span
          >
          <a
            class="font-semibold hover:highlighter"
            href={resolve("/[...path]", { path: match.path ?? "" })}
            >{first?.name}</a
          >
          {#each match.pair as mark (mark.id)}
            <span
              class="col-start-2 grid grid-cols-[minmax(0,1fr)_auto_minmax(0,3rem)] items-center gap-x-3 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,8rem)]"
            >
              <span class="flex items-center gap-1.5 truncate">
                {@render glyph(mark.side, mark.isWon, mark.tint)}
                <span class={cn("truncate", { "font-semibold": mark.isWon })}
                  >{sides[mark.side]?.name}</span
                >
                {#if mark.outcome != null}
                  <span
                    class={cn("px-1 text-xs font-semibold whitespace-nowrap", {
                      highlighter: !LOST.has(mark.outcome),
                      "rounded-xs bg-secondary text-muted-foreground": LOST.has(
                        mark.outcome,
                      ),
                    })}>{OUTCOMES[mark.outcome]?.() ?? mark.outcome}</span
                  >
                {/if}
              </span>
              <span class="font-figures text-[0.8125rem] tabular-nums"
                >{mark.share == null ? "–" : percent(mark.share)}</span
              >
              <span
                class="flex overflow-hidden rounded-xs bg-muted block-1.5"
                aria-hidden="true"
              >
                <span
                  style:inline-size="{(mark.share ?? 0) * 100}%"
                  style:background-color={mark.tint}
                ></span>
              </span>
            </span>
          {/each}
        </li>
      {/each}
    </ol>
  {/if}
</section>
