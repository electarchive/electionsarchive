<script lang="ts">
  import type { LiveOffice } from "@ea/analytics/live";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import type { Summary } from "#lib/live/summary.js";
  import { percent } from "#lib/format.js";
  import { MUTED, short, strength } from "#lib/live/board/labels.js";
  import { lead, surname, tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import Atlas from "./atlas.svelte";

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
  const fills = $derived.by(() => {
    const found: Record<string, string> = {};
    for (const [code, areaId] of Object.entries(shapes?.codes ?? {})) {
      const races = byState.get(code) ?? [];
      found[areaId] = races.length === 0 ? MUTED : strength(lean(races));
    }
    return found;
  });
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
    const groups = Map.groupBy(races, (summary) =>
      summary.winner == null && summary.leader == null
        ? ""
        : (summary.winner ?? summary.leader)?.party?.id,
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
    {const option = $derived(summary.winner ?? summary.leader)}
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
            >+{lead(summary.margin)}</span
          >
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
  <div
    class="grid animate-pulse place-items-center rounded-xs bg-muted text-[0.75em] text-muted-foreground block-full motion-reduce:animate-none"
  >
    {m.board_loading()}
  </div>
{:else}
  <Atlas
    {shapes}
    {fills}
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
