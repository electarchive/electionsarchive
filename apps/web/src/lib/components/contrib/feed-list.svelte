<script lang="ts">
  import type { FeedEntry } from "#lib/contrib.js";
  import { byline, describe } from "#lib/contrib.js";
  import { date, dates } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    entries,
    label,
    marks,
  }: {
    entries: Array<FeedEntry>;
    label: string;
    marks?: Record<
      string,
      { reason: "direct" | "watched"; unread: boolean } | undefined
    >;
  } = $props();

  const reasonLabel = {
    direct: m.notifications_reason_direct,
    watched: m.notifications_reason_watched,
  };

  const clock = dates({ hour: "numeric", minute: "2-digit", timeZone: "UTC" });

  const days = $derived.by(() => {
    const grouped: Array<{ day: string; entries: Array<FeedEntry> }> = [];
    for (const entry of entries) {
      const day = entry.at.slice(0, 10);
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
          {const described = $derived(describe(entry))}
          {const mark = $derived(marks?.[entry.id])}
          <li
            class={cn(
              "relative grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 border-be py-2 text-sm max-sm:grid-cols-1",
              { "font-medium": mark?.unread === true },
            )}
          >
            {#if mark?.unread === true}
              <span
                class="absolute -inset-s-3 inset-bs-3.5 rounded-full bg-foreground block-1.5 inline-1.5 max-sm:-inset-s-2.5"
                aria-hidden="true"
              ></span>
              <span class="sr-only">{m.notifications_unread()}</span>
            {/if}
            <time
              class="pbs-0.5 font-figures text-xs text-muted-foreground"
              datetime={entry.at}>{clock.format(new Date(entry.at))} UTC</time
            >
            <div class="grid gap-0.5 min-inline-0">
              {#if described.href == null}
                <span class="text-pretty">{described.text}</span>
              {:else}
                <a
                  class="text-pretty underline-offset-4 hover:underline"
                  href={described.href}>{described.text}</a
                >
              {/if}
              <span
                class="flex flex-wrap gap-x-3 text-xs text-muted-foreground"
              >
                {#if entry.handle == null}
                  <span>{byline(entry)}</span>
                {:else}
                  <a
                    class="underline-offset-4 hover:text-foreground hover:underline"
                    href="/contributors/{entry.handle}">{byline(entry)}</a
                  >
                {/if}
                {#if entry.polity != null}
                  {#if entry.polity_path == null}
                    <span>{entry.polity}</span>
                  {:else}
                    <a
                      class="underline-offset-4 hover:text-foreground hover:underline"
                      href="/{entry.polity_path}">{entry.polity}</a
                    >
                  {/if}
                {/if}
                {#if mark != null}
                  <span>{reasonLabel[mark.reason]()}</span>
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
