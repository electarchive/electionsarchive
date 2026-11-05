<script lang="ts">
  import type { LiveOffice } from "@ea/analytics/live";
  import { Pending } from "svelte-islands";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import type { Summary } from "#lib/live/summary.js";
  import { percent } from "#lib/format.js";
  import { ahead, MUTED, short, strength } from "#lib/live/board/labels.js";
  import { surname, tone } from "#lib/live/format.js";
  import { edge } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import Atlas from "./atlas.svelte";
  import Mark from "./mark.svelte";

  const { desk, office = "senate" }: { desk: Desk; office?: LiveOffice } =
    $props();

  let hovered = $state<string | null>(null);

  const path = $derived(desk.feed.election.slug);
  const shapes = $derived(desk.feed.shapes(path));
  const codeOf = $derived(
    new Map(
      Object.entries(shapes?.codes ?? {}).map(([code, areaId]) => [
        areaId,
        code,
      ]),
    ),
  );
  const byState = $derived(
    Map.groupBy(
      [...desk.feed.summaries.values()].filter(
        (summary) => summary.race.office === office,
      ),
      (summary) => summary.race.state,
    ),
  );
  const marks = $derived(
    new Map(
      Object.entries(shapes?.codes ?? {}).flatMap(([code, areaId]) => {
        const first = office === "house" ? null : byState.get(code)?.[0];
        const mark = first == null ? null : desk.hatch(first);
        return mark == null ? [] : [[areaId, mark] as const];
      }),
    ),
  );
  const fills = $derived.by(() => {
    const found: Record<string, string> = {};
    for (const [code, areaId] of Object.entries(shapes?.codes ?? {})) {
      const races = byState.get(code) ?? [];
      found[areaId] =
        marks.get(areaId)?.fill ??
        (races.length === 0 ? MUTED : strength(lean(races)));
    }
    return found;
  });
  const flips = $derived(
    Object.fromEntries(
      [...marks].map(([areaId, mark]) => [areaId, mark.stripe]),
    ),
  );
  const marked = $derived(
    desk.selected?.office === office
      ? (shapes?.codes[desk.selected.state] ?? null)
      : null,
  );

  $effect(() => {
    desk.feed.wantShapes(path);
  });

  function lean(races: Array<Summary>): Summary | undefined {
    if (office !== "house") {
      return races[0];
    }
    const groups = Map.groupBy(
      races,
      (summary) => ahead(summary)?.party?.id ?? "",
    );
    let best: Array<Summary> = [];
    for (const [key, group] of groups) {
      if (key !== "" && group.length > best.length) {
        best = group;
      }
    }
    return best[0];
  }
</script>

{#snippet tip(areaId: string)}
  {const code = $derived(codeOf.get(areaId) ?? "")}
  {const races = $derived(byState.get(code) ?? [])}
  <span class="font-semibold">{code.toUpperCase()}</span>
  {#if races.length === 0}
    <span class="text-muted-foreground">{m.night_nation_none()}</span>
  {/if}
  {#each races.slice(0, 4) as summary (summary.race.id)}
    {const option = $derived(ahead(summary))}
    <span class="flex items-center justify-between gap-3">
      <span class="text-muted-foreground">{short(summary.race)}</span>
      {#if option == null}
        <span class="text-muted-foreground">{m.night_nation_waiting()}</span>
      {:else}
        <span class="flex items-center gap-1.5">
          <span
            class="rounded-full block-2 inline-2"
            style:background-color={tone(option)}
          ></span>
          {surname(option)}
          <span class="font-figures text-muted-foreground tabular-nums"
            >{edge(summary)}</span
          >
          <Mark {summary} />
        </span>
      {/if}
    </span>
    {#if summary.counted != null && races.length === 1}
      <span class="text-muted-foreground"
        >{m.board_in({ share: percent(summary.counted, 0) })}</span
      >
    {/if}
  {/each}
{/snippet}

{#if shapes == null}
  <Pending
    name="live.board.nation"
    isPending={true}
    reserve="100%"
    class="[--island-inset:0]"
  />
{:else}
  <Atlas
    {shapes}
    {fills}
    {flips}
    {marked}
    label={m.board_kind_nation()}
    bind:hovered
    onPick={(areaId: string) => {
      const race = byState.get(codeOf.get(areaId) ?? "")?.[0]?.race;
      if (race != null) {
        desk.select(race.slug);
      }
    }}
    {tip}
  />
{/if}
