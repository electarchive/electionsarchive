<script lang="ts">
  import type { Entry } from "#lib/live/ballot.js";
  import { count, percent } from "#lib/format.js";
  import { sides } from "#lib/live/local.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const { entry, href }: { entry: Entry; href: string } = $props();

  const summary = $derived(entry.summary);
  const pair = $derived(sides(summary.race));
  const yes = $derived(pair == null ? 0 : (summary.totals[pair.yes] ?? 0));
  const no = $derived(pair == null ? 0 : (summary.totals[pair.no] ?? 0));
  const total = $derived(yes + no);
  const pick = $derived(summary.winner ?? summary.leader);
  const isYes = $derived(
    pair == null || pick == null
      ? null
      : pick.id === summary.race.options[pair.yes]?.id,
  );
  const verdict = $derived.by(() => {
    if (isYes == null || total === 0) {
      return null;
    }
    if (summary.winner != null) {
      return isYes ? m.night_local_passed() : m.night_local_failed();
    }
    return isYes ? m.night_local_passing() : m.night_local_failing();
  });
</script>

<a
  {href}
  class="grid gap-2 py-3 outline-none hover:highlighter focus-visible:highlighter"
>
  <span class="flex items-baseline justify-between gap-3">
    <span class="text-[0.9375rem] leading-snug font-semibold text-pretty"
      >{entry.contest.title}</span
    >
    {#if verdict != null}
      <span
        class={cn("shrink-0 text-xs font-bold", {
          "text-(--yes)": isYes === true,
          "text-(--no)": isYes === false,
        })}
        >{#if summary.winner != null}✓{/if}
        {verdict}</span
      >
    {/if}
  </span>
  {#if pair != null}
    <span class="grid gap-1">
      <span
        class="flex justify-between gap-3 font-figures text-xs tabular-nums"
      >
        <span class="text-(--yes)"
          ><span class="font-sans font-semibold"
            >{summary.race.options[pair.yes]?.label}</span
          >
          {total > 0 ? percent(yes / total) : ""}
          <span class="text-muted-foreground"
            >{total > 0 ? count(yes) : ""}</span
          ></span
        >
        <span class="text-(--no)"
          ><span class="text-muted-foreground"
            >{total > 0 ? count(no) : ""}</span
          >
          {total > 0 ? percent(no / total) : ""}
          <span class="font-sans font-semibold"
            >{summary.race.options[pair.no]?.label}</span
          ></span
        >
      </span>
      <span
        class="relative flex overflow-hidden rounded-xs bg-muted block-2"
        aria-hidden="true"
      >
        {#if total > 0}
          <span
            class="bg-(--yes) transition-[inline-size] duration-700 motion-reduce:transition-none"
            style:inline-size="{(yes / total) * 100}%"
          ></span>
          <span class="flex-1 bg-(--no)"></span>
        {/if}
        <span class="absolute inset-y-0 inset-s-1/2 bg-background inline-px"
        ></span>
      </span>
    </span>
  {/if}
  <span class="font-figures text-xs text-muted-foreground tabular-nums"
    >{summary.counted == null
      ? m.night_nation_waiting()
      : m.night_tip_counted({ share: percent(summary.counted, 0) })}</span
  >
</a>
