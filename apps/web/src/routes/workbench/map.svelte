<script lang="ts">
  import PlaceMap from "#routes/[...path]/map.svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import type { Contest, Desk } from "./desk.svelte.js";

  const {
    id,
    contest,
    desk,
    span,
  }: {
    id: string;
    contest: Contest;
    desk: Desk;
    span: { from: string; to: string; forward: boolean } | null;
  } = $props();

  const outlines = createQuery(() => ({
    ...orpc.contests.boundaries.queryOptions({
      input: { id, level: contest.places?.level },
    }),
    enabled: contest.places != null,
  }));
  const drawn = $derived.by(() => {
    const places = contest.places;
    const found = outlines.data ?? [];
    const level = found.find((entry) => entry.scheme === places?.level);
    const own = found.find(
      (entry) => entry.k != null && entry.scheme !== places?.level,
    );
    return places == null || level?.tiles == null || own?.frame == null
      ? null
      : {
          places,
          tiles: level.tiles,
          own:
            own.tiles == null || own.k == null
              ? null
              : { tiles: own.tiles, k: own.k },
          frame: own.frame,
          approximate: level.approximate,
          noun:
            places.levels.find((entry) => entry.code === places.level)?.noun ??
            level.noun,
        };
  });
  const legend = $derived(contest.places?.legend ?? []);
  const names = $derived(
    new Map((contest.places?.rows ?? []).map((row) => [row.k, row.name])),
  );
  const keys = $derived(
    new Map((contest.places?.rows ?? []).map((row) => [row.name, row.k])),
  );
</script>

{#if drawn == null}
  <p class="text-[0.75em] text-muted-foreground">
    {outlines.isPending && contest.places != null ? "" : m.workbench_map_none()}
  </p>
{:else}
  {#key drawn.tiles}
    <PlaceMap
      places={drawn.places}
      colors={legend.map((item) => swatch(item.color, item.key))}
      tiles={drawn.tiles}
      own={drawn.own}
      frame={drawn.frame}
      approximate={drawn.approximate}
      noun={drawn.noun}
      votes={m.col_votes()}
      names={legend.map((item) =>
        item.person == null ? (item.short ?? item.label) : item.label,
      )}
      levels={[]}
      {span}
      bind:focus={
        () => (desk.hovered == null ? null : (keys.get(desk.hovered) ?? null)),
        (next: number | null) => {
          desk.hovered = next == null ? null : (names.get(next) ?? null);
        }
      }
      onopen={(k: number) => {
        const name = names.get(k);
        if (name != null) {
          desk.toggle(name);
        }
      }}
    />
  {/key}
{/if}
