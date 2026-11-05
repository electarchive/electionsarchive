<script lang="ts">
  import type { Summary } from "#lib/live/summary.js";
  import { short } from "#lib/live/board/labels.js";
  import { clock, tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    summaries,
    href,
  }: { summaries: Array<Summary>; href: (summary: Summary) => string } =
    $props();

  const LIMIT = 8;

  let isAll = $state(false);

  const calls = $derived(
    summaries
      .flatMap((summary) => {
        const option = summary.winner ?? summary.projected;
        return option == null
          ? []
          : [
              {
                summary,
                option,
                color: tone(option),
                at: summary.race.projected?.at ?? summary.at,
              },
            ];
      })
      .toSorted((a, b) => (b.at ?? "").localeCompare(a.at ?? "")),
  );
  const shown = $derived(isAll ? calls : calls.slice(0, LIMIT));

  function mix(color: string, share: number, base = "var(--background)") {
    return `color-mix(in oklab, ${color} ${share}%, ${base})`;
  }
</script>

<div class="grid content-start gap-3">
  <div class="grid gap-0.5">
    <h2 id="night-calls" class="heading text-lg!">{m.night_calls_title()}</h2>
    <p class="text-xs text-pretty text-muted-foreground">
      {m.night_calls_hint()}
    </p>
  </div>
  {#if calls.length === 0}
    <p class="text-sm text-muted-foreground">{m.night_calls_empty()}</p>
  {:else}
    <ol class="grid border-bs border-border text-[0.8125rem]">
      {#each shown as call (call.summary.race.id)}
        <li>
          <a
            href={href(call.summary)}
            class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-stretch gap-x-3 border-be border-border/60 py-2 pe-1 hover:bg-muted"
          >
            <span
              class="rounded-xs inline-1"
              style:background-color={call.color}
              aria-hidden="true"
            ></span>
            <span class="grid gap-0.5 min-inline-0">
              <span class="truncate font-semibold"
                >{short(call.summary.race)}</span
              >
              <span class="flex items-center gap-1.5 min-inline-0">
                <span class="truncate">{call.option.label}</span>
                {#if call.option.party != null}
                  <span
                    class="inline-flex shrink-0 items-center rounded-xs px-1 text-[11px]/4 font-semibold whitespace-nowrap"
                    style:color={mix(call.color, 72, "var(--foreground)")}
                    style:background-color={mix(call.color, 16)}
                    style:box-shadow="inset 0 0 0 1px {mix(call.color, 45)}"
                    >{call.option.party.short}</span
                  >
                {/if}
              </span>
            </span>
            <span class="grid content-center justify-items-end gap-0.5">
              <span
                class="font-figures font-semibold whitespace-nowrap tabular-nums"
                >{clock(call.at)}</span
              >
              <span class="text-xs text-muted-foreground"
                >{call.summary.winner == null
                  ? m.night_rating_projected()
                  : m.night_rating_won()}</span
              >
            </span>
          </a>
        </li>
      {/each}
    </ol>
    {#if calls.length > LIMIT}
      <button
        type="button"
        class="justify-self-start text-xs font-semibold underline decoration-current/45 underline-offset-[0.2em] hover:decoration-current"
        aria-expanded={isAll}
        onclick={() => (isAll = !isAll)}
        >{isAll
          ? m.night_calls_less()
          : m.night_calls_more({ count: calls.length })}</button
      >
    {/if}
  {/if}
</div>
