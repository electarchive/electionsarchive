<script lang="ts">
  import Name from "#lib/components/name.svelte";
  import { date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import Preview from "./preview.svelte";
  import { OUTCOMES } from "./results.svelte";

  const {
    rounds,
  }: {
    rounds: Array<{
      id: string;
      path: string | null;
      round: number;
      kind: string;
      date: string | null;
      isCurrent: boolean;
      leaders: Array<{
        key: string;
        label: string;
        color: string | null;
        person: string | null;
        party: string | null;
        share: number;
        outcome: string | null;
      }>;
    }>;
  } = $props();

  const uid = $props.id();
  const isDated = $derived(new Set(rounds.map((round) => round.date)).size > 1);
</script>

<nav
  class="col-span-12 grid bg-card px-3.5 pbs-2.5 pbe-3"
  aria-labelledby="{uid}-title"
>
  <h2 id="{uid}-title" class="sr-only">{m.rounds_title()}</h2>
  <ol
    class="grid grid-cols-[repeat(auto-fit,minmax(min(100%,14rem),1fr))] gap-x-3 gap-y-3.5"
  >
    {#each rounds as round, index (round.id)}
      {const label = $derived(
        round.kind === "primary"
          ? m.round_primary()
          : round.kind === "runoff"
            ? m.round_runoff()
            : round.kind === "indirect"
              ? rounds.length === 2
                ? index === 0
                  ? m.round_electoral()
                  : m.round_indirect()
                : m.round_ballot({ round: index + 1 })
              : round.kind === "general"
                ? index === 0
                  ? m.round_first()
                  : m.round_general()
                : m.round_number({ round: index + 1 }),
      )}
      <li
        class={cn("grid content-start gap-1.5 border-bs-2 pbs-2", {
          "border-foreground": round.isCurrent,
          "border-border": !round.isCurrent,
        })}
        aria-current={round.isCurrent ? "step" : undefined}
      >
        <p class="flex flex-wrap items-baseline gap-x-2 text-[0.8125rem]">
          {#if round.isCurrent || round.path == null}
            <span class="font-semibold">{label}</span>
          {:else}
            <Preview
              class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
              path={round.path}>{label}</Preview
            >
          {/if}
          {#if isDated && round.date != null}
            <time class="text-muted-foreground" datetime={round.date}
              >{date(round.date)}</time
            >
          {/if}
        </p>
        <ul class="grid gap-0.5 text-sm">
          {#each round.leaders as leader (leader.key)}
            <li
              class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3"
            >
              <span class="flex items-baseline gap-1.5 min-inline-0">
                <Name
                  class={cn("truncate min-inline-0", {
                    "font-semibold":
                      leader.outcome === "elected" || leader.outcome === "won",
                  })}
                  label={leader.label}
                  color={swatch(leader.color, leader.key)}
                  person={leader.person}
                  party={leader.party}
                />
                {#if leader.outcome != null}
                  <span class="shrink-0 text-xs text-muted-foreground"
                    >{OUTCOMES[leader.outcome]?.() ?? leader.outcome}</span
                  >
                {/if}
              </span>
              <span class="font-figures text-[0.8125rem] tabular-nums"
                >{percent(leader.share)}</span
              >
            </li>
          {/each}
        </ul>
      </li>
    {/each}
  </ol>
</nav>
