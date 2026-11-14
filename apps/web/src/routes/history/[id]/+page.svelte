<script lang="ts">
  import type { HistoryEntry } from "#lib/changes.js";
  import { importedBy, roleLabel } from "#lib/changes.js";
  import Moment from "#lib/components/moment.svelte";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { localDay } from "#lib/zone.svelte.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const days = $derived.by(() => {
    const grouped: Array<{ day: string; entries: Array<HistoryEntry> }> = [];
    for (const entry of data.entries) {
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

<svelte:head>
  <title>{m.history_page_title({ name: data.entity.name })}</title>
  <meta name="robots" content="noindex" />
  <link
    rel="alternate"
    type="application/atom+xml"
    title={m.history_feed_title({ name: data.entity.name })}
    href="/history/{data.entity.public_id}.atom"
  />
</svelte:head>

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-3">
    <h1 class="title text-balance">{data.entity.name}</h1>
    <p class="text-sm">
      <a class="underline underline-offset-4" href="/id/{data.entity.id}"
        >{m.history_record()}</a
      >
    </p>
  </header>

  {#if data.entries.length === 0}
    <p class="mbs-8 border-bs pbs-3 text-sm text-pretty text-muted-foreground">
      {m.history_empty()}
    </p>
  {:else}
    <div class="mbs-8 grid gap-6">
      {#each days as group (group.day)}
        <section aria-labelledby="day-{group.day}">
          <h2
            id="day-{group.day}"
            class="border-be pbe-1.5 text-sm font-semibold"
          >
            {date(group.day)}
          </h2>
          <ol class="grid">
            {#each group.entries as entry (entry.changeset)}
              <li
                class="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 border-be py-2 text-sm max-sm:grid-cols-1"
              >
                <Moment
                  at={entry.at}
                  style="time"
                  class="pbs-0.5 font-figures text-xs text-muted-foreground"
                />
                <div class="grid gap-0.5 min-inline-0">
                  <a
                    class="text-pretty underline-offset-4 hover:underline"
                    href="/history/{data.entity.public_id}/{entry.changeset}"
                    >{entry.summary}</a
                  >
                  <span
                    class="flex flex-wrap gap-x-3 text-xs text-muted-foreground"
                  >
                    <span class="font-semibold text-foreground"
                      >{roleLabel[entry.role]()}</span
                    >
                    <span>{importedBy(entry.pipeline)}</span>
                  </span>
                </div>
              </li>
            {/each}
          </ol>
        </section>
      {/each}
    </div>
  {/if}

  {#if data.paged || data.next != null}
    <p class="mbs-6 flex gap-5 text-sm">
      {#if data.paged}
        <a
          class="underline underline-offset-4"
          href="/history/{data.entity.public_id}">{m.history_newest()}</a
        >
      {/if}
      {#if data.next != null}
        <a
          class="underline underline-offset-4"
          href="/history/{data.entity.public_id}?before={encodeURIComponent(
            data.next,
          )}">{m.history_older()}</a
        >
      {/if}
    </p>
  {/if}
</main>
