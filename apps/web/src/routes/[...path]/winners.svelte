<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import Download from "#lib/components/download.svelte";
  import { Input } from "#lib/components/ui/input/index.js";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import PlaceMap from "./map.svelte";
  import Preview from "./preview.svelte";

  const {
    id,
    parts,
  }: {
    id: string;
    parts: Array<{ path: string | null; place: string | null; name: string }>;
  } = $props();

  const uid = $props.id();
  let query = $state("");
  let opened = $state<Array<string>>([]);
  let level = $state<string | null>(null);
  let isAll = $state(false);

  const winners = createQuery(() => ({
    ...orpc.elections.winners.queryOptions({ input: { id } }),
    staleTime: Infinity,
    meta: { quiet: true },
  }));

  const places = $derived(
    new Map(parts.map((part) => [part.path, part.place ?? part.name])),
  );
  const noun = $derived(winners.data?.noun ?? "");
  const maps = $derived(winners.data?.maps ?? []);
  const shape = $derived(
    maps.find((entry) => entry.places.level === level) ?? maps[0],
  );
  const groups = $derived(
    (winners.data?.groups ?? []).map((group) => {
      const rows = (winners.data?.rows ?? [])
        .filter((row) => row.group === group.id)
        .map((row) => ({
          ...row,
          place: places.get(row.path) ?? row.path,
          tone: swatch(
            row.color,
            row.party == null
              ? `person:${row.person ?? ""}`
              : `party:${row.party}`,
          ),
          isCenter: row.path.split("/").length === 3,
        }))
        .toSorted(
          (a, b) =>
            Number(b.isCenter) - Number(a.isCenter) ||
            a.place.localeCompare(b.place, "tr"),
        );
      const tally = Map.groupBy(
        rows.filter((row) => row.party != null),
        (row) => row.party,
      )
        .values()
        .map((members) => ({
          key: members[0]?.party ?? "",
          short: members[0]?.short ?? "",
          tone: members[0]?.tone ?? "",
          wins: members.length,
        }))
        .toArray()
        .toSorted((a, b) => b.wins - a.wins);
      return { ...group, rows, tally };
    }),
  );
  const shown = $derived.by(() => {
    const needle = query.trim().toLocaleLowerCase("tr");
    return needle === ""
      ? groups
      : groups.flatMap((group) => {
          if (group.name.toLocaleLowerCase("tr").includes(needle)) {
            return [group];
          }
          const rows = group.rows.filter((row) =>
            row.place.toLocaleLowerCase("tr").includes(needle),
          );
          return rows.length === 0 ? [] : [{ ...group, rows }];
        });
  });
</script>

{#if groups.length > 0}
  <section
    class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="{uid}-title"
  >
    <div class="flex flex-wrap items-center gap-x-4 gap-y-2.5">
      <div class="grid gap-0.5">
        <h2 id="{uid}-title" class="heading">
          {m.winners_title({ noun })}
        </h2>
        <p class="text-[0.8125rem] text-pretty text-muted-foreground">
          {m.winners_lede({
            count: count(winners.data?.rows.length ?? 0),
            noun,
          })}
        </p>
      </div>
      <Input
        class="ms-auto block-8 inline-56 max-sm:inline-full"
        type="search"
        placeholder={m.places_filter()}
        aria-label={m.places_filter()}
        bind:value={query}
      />
      <Download
        name="winners"
        columns={[noun, m.col_option(), m.col_party(), "url"]}
        rows={() =>
          groups.flatMap((group) =>
            group.rows.map((row) => [
              group.name,
              row.place,
              row.label,
              row.short,
              new URL(`/${row.path}`, location.origin).href,
            ]),
          )}
      />
    </div>
    {#if shape != null}
      {#key shape.places.level}
        <PlaceMap
          places={shape.places}
          colors={shape.places.legend.map((item) =>
            swatch(item.color, item.key),
          )}
          tiles={shape.map.tiles}
          own={shape.map.own}
          frames={shape.map.frames}
          isApproximate={shape.map.isApproximate}
          sources={shape.map.credits}
          noun={shape.places.levels[0]?.noun ?? ""}
          votes=""
          names={shape.places.legend.map((item) => item.short ?? item.label)}
          levels={maps.map((entry) => {
            const name = entry.places.levels[0]?.noun ?? "";
            return {
              code: entry.places.level,
              label: name.charAt(0).toUpperCase() + name.slice(1),
              count: entry.places.rows.length,
              path: "",
              query: "",
              isCurrent: entry === shape,
            };
          })}
          pick={(code) => (level = code)}
          span={null}
        />
      {/key}
    {/if}
    {#if shown.length === 0}
      <p class="py-3 text-sm text-muted-foreground">
        {m.places_filter_empty({ query })}
      </p>
    {/if}
    <ul
      class="grid gap-px overflow-hidden rounded-sm bg-border ring-1 ring-border sm:grid-cols-2 xl:grid-cols-3"
    >
      {#each isAll || query !== "" ? shown : shown.slice(0, 12) as group (group.id)}
        {const isOpen = $derived(opened.includes(group.id) || query !== "")}
        {const total = $derived(group.rows.length)}
        <li class="grid content-start gap-2 bg-card px-3 pbs-2.5 pbe-3">
          <div class="flex items-baseline justify-between gap-3">
            <h3 class="font-semibold">{group.name}</h3>
            <span
              class="font-figures text-xs text-muted-foreground tabular-nums"
              >{count(total)}</span
            >
          </div>
          {#if group.tally.length > 0}
            <div class="grid gap-1">
              <div
                class="flex overflow-hidden rounded-xs bg-secondary block-1.5"
                role="img"
                aria-label={group.tally
                  .map((entry) => `${entry.short} ${count(entry.wins)}`)
                  .join(", ")}
              >
                {#each group.tally as entry (entry.key)}
                  <span
                    class="bg-(--party) block-full"
                    style:--party={entry.tone}
                    style:inline-size="{(entry.wins / total) * 100}%"
                  ></span>
                {/each}
              </div>
              <p class="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                {#each group.tally.slice(0, 4) as entry (entry.key)}
                  <span
                    ><span class="font-semibold text-foreground"
                      >{entry.short}</span
                    >
                    <span class="font-figures tabular-nums"
                      >{count(entry.wins)}</span
                    ></span
                  >
                {/each}
              </p>
            </div>
          {/if}
          <ul class="grid text-sm/tight">
            {#each isOpen ? group.rows : group.rows.slice(0, 4) as row (row.path)}
              <li
                class="flex items-baseline gap-2 border-be border-border/60 py-1.5 min-inline-0 last:border-be-0"
                style:--party={row.tone}
              >
                <span
                  class="shrink-0 self-center rounded-xs bg-(--party) block-2.5 inline-2.5"
                ></span>
                <Preview
                  class={cn("truncate hover:highlighter", {
                    "font-semibold": row.isCenter,
                  })}
                  path={row.path}>{row.place}</Preview
                >
                <span
                  class="ms-auto truncate text-end text-[0.8125rem] text-muted-foreground"
                >
                  {#if row.label == null}
                    {m.winners_none()}
                  {:else}
                    {row.label}{#if row.short != null && row.short !== row.label}
                      <span class="ms-1 font-semibold text-foreground">
                        {row.short}</span
                      >{/if}
                  {/if}
                </span>
              </li>
            {/each}
          </ul>
          {#if total > 4 && query === ""}
            <button
              type="button"
              class="justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2"
              aria-expanded={isOpen}
              onclick={() =>
                (opened = isOpen
                  ? opened.filter((entry) => entry !== group.id)
                  : [...opened, group.id])}
              >{isOpen
                ? m.winners_fewer()
                : m.winners_more({ count: count(total) })}</button
            >
          {/if}
        </li>
      {/each}
    </ul>
    {#if shown.length > 12 && query === ""}
      <button
        type="button"
        class="justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2"
        aria-expanded={isAll}
        onclick={() => (isAll = !isAll)}
        >{isAll
          ? m.winners_fewer()
          : m.winners_more({ count: count(shown.length) })}</button
      >
    {/if}
  </section>
{/if}
