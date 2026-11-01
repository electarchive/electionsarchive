<script lang="ts">
  import { goto } from "$app/navigation";
  import type { Entry } from "#lib/live/ballot.js";
  import type { Geography } from "#lib/live/standing.js";
  import Nation from "#lib/components/live/nation.svelte";
  import Card from "#lib/components/live/state/card.svelte";
  import Sheet from "#lib/components/live/state/sheet.svelte";
  import { m } from "#lib/paraglide/messages.js";

  const {
    geography,
    entries,
    name,
    label,
    href,
    shade,
  }: {
    geography: Geography;
    entries: Array<Entry>;
    name: string;
    label: (entry: Entry) => string;
    href: (entry: Entry) => string;
    shade: (entry: Entry) => string;
  } = $props();

  const byArea = $derived(
    Map.groupBy(
      entries.filter((entry) => entry.contest.area != null),
      (entry) => entry.contest.area ?? "",
    ),
  );
  const codes = $derived(
    Object.fromEntries([...byArea.keys()].map((area) => [area, area])),
  );
  const colors = $derived(
    Object.fromEntries(
      [...byArea].flatMap(([area, list]) => {
        const first = list.find((entry) => !entry.contest.isSpecial) ?? list[0];
        return first == null ? [] : [[area, shade(first)] as const];
      }),
    ),
  );

  let hovered = $state<string | null>(null);
  let picked = $state<string | null>(null);
  let isSheet = $state(false);
</script>

{#snippet cards(area: string, hasHeading: boolean)}
  {const list = $derived(byArea.get(area) ?? [])}
  <div class="grid gap-3 divide-y divide-border">
    {#each list as entry (entry.contest.id)}
      <div class="pbe-3 last:pbe-0">
        <Card
          summary={entry.summary}
          heading={hasHeading || list.length > 1
            ? entry.contest.isSpecial
              ? entry.contest.title
              : label(entry)
            : undefined}
          held={entry.held}
          isFlip={entry.isFlip}
        />
      </div>
    {/each}
  </div>
{/snippet}

{#snippet tip(area: string)}
  <div class="inline-[min(18rem,calc(100vw-4rem))]">
    {@render cards(area, true)}
  </div>
{/snippet}

<Nation
  {geography}
  {codes}
  {colors}
  label={m.night_state_districts_label({ chamber: name })}
  bind:hovered
  onPick={async (area: string) => {
    const first = byArea.get(area)?.[0];
    if (first == null) {
      return;
    }
    if (matchMedia("(hover: none)").matches) {
      picked = area;
      isSheet = true;
      return;
    }
    await goto(href(first));
  }}
  {tip}
/>

{#if picked != null}
  {const first = $derived(byArea.get(picked)?.[0] ?? null)}
  {#if first != null}
    <Sheet
      bind:isOpen={isSheet}
      title={`${name}, ${label(first)}`}
      href={href(first)}
      action={m.night_state_results()}
    >
      {@render cards(picked, false)}
    </Sheet>
  {/if}
{/if}
