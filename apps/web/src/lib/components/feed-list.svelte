<script lang="ts">
  import type { FeedEntry } from "#lib/changes.js";
  import { importedBy } from "#lib/changes.js";
  import Moment from "#lib/components/moment.svelte";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { localDay } from "#lib/zone.svelte.js";

  const {
    entries,
    label,
  }: {
    entries: Array<FeedEntry>;
    label: string;
  } = $props();

  const days = $derived.by(() => {
    const grouped: Array<{ day: string; entries: Array<FeedEntry> }> = [];
    for (const entry of entries) {
      const day = localDay(entry.at);
      const last = grouped.at(-1);
      if (last?.day === day) {
        last.entries.push(entry);
      } else {
        grouped.push({ day, entries: [entry] });
      }
    }
    return grouped;
  });
</script>

<section aria-label={label} class="grid gap-6">
  {#each days as group (group.day)}
    <section aria-labelledby="day-{group.day}">
      <h2 id="day-{group.day}" class="border-be pbe-1.5 text-sm font-semibold">
        {date(group.day)}
      </h2>
      <ol class="grid">
        {#each group.entries as entry (entry.id)}
          <li
            class="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 border-be py-2 text-sm max-sm:grid-cols-1"
          >
            <Moment
              at={entry.at}
              style="time"
              class="pbs-0.5 font-figures text-xs text-muted-foreground"
            />
            <div class="grid gap-1.5 min-inline-0">
              {#if entry.polity_path == null}
                <span class="text-pretty">{entry.title}</span>
              {:else}
                <a
                  class="-my-0.5 py-0.5 text-pretty underline-offset-4 hover:underline"
                  href="/{entry.polity_path}">{entry.title}</a
                >
              {/if}
              <span
                class="flex flex-wrap gap-x-3 text-xs text-muted-foreground"
              >
                <span>{importedBy(entry.pipeline)}</span>
                {#if entry.polity != null}
                  {#if entry.polity_path == null}
                    <span>{entry.polity}</span>
                  {:else}
                    <a
                      class="-my-1 py-1 underline-offset-4 hover:text-foreground hover:underline"
                      href="/{entry.polity_path}">{entry.polity}</a
                    >
                  {/if}
                {/if}
                {#if entry.batches > 1}
                  <span class="font-figures"
                    >{m.feed_batches({ count: entry.batches })}</span
                  >
                {/if}
              </span>
            </div>
          </li>
        {/each}
      </ol>
    </section>
  {/each}
</section>
