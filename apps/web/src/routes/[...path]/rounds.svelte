<script lang="ts">
  import { resolve } from "$app/paths";
  import Name from "#lib/components/name.svelte";
  import { count, date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Preview from "./preview.svelte";

  const {
    rounds,
    cycle,
  }: {
    rounds: Extract<PageData, { kind: "contest" }>["rounds"];
    cycle: Extract<PageData, { kind: "contest" }>["cycle"];
  } = $props();

  const uid = $props.id();

  const span = (from: string | null, to: string | null) => {
    if (from == null) {
      return "";
    }
    if (to == null) {
      return date(from);
    }
    return new Intl.DateTimeFormat(getLocale(), {
      dateStyle: "medium",
      timeZone: "UTC",
    }).formatRange(new Date(`${from}T00:00:00Z`), new Date(`${to}T00:00:00Z`));
  };

  const columns = $derived.by(() => {
    const decided = (winners: (typeof cycle.primaries)[number]["winners"]) =>
      winners.map((winner) => ({
        key: winner.person ?? winner.party ?? winner.label,
        label: winner.label,
        color: winner.color,
        person: winner.person,
        party: winner.party,
        share: winner.share,
      }));
    const parties = cycle.primaries.flatMap((primary) => {
      const party = primary.party?.short ?? primary.party?.label ?? null;
      const tone = primary.party?.color ?? null;
      return [
        {
          id: primary.id,
          label:
            party == null
              ? m.round_primary()
              : (primary.until == null ? m.cycle_primary : m.cycle_primaries)({
                  party,
                }),
          when: span(primary.date, primary.until),
          path: primary.path,
          isCurrent: primary.isCurrent,
          tone,
          leaders: decided(primary.winners),
          local: primary.local,
        },
        ...(primary.runoff == null
          ? []
          : [
              {
                id: primary.runoff.id,
                label: m.round_runoff(),
                when: span(primary.runoff.date, null),
                path: primary.runoff.path,
                isCurrent: primary.runoff.isCurrent,
                tone,
                leaders: decided(primary.runoff.winners),
                local: null,
              },
            ]),
      ];
    });
    const known = new Set(parties.map((stage) => stage.id));
    const generals = rounds.filter((round) => round.kind === "general");
    const chained = rounds
      .filter((round) => !known.has(round.id))
      .map((round) => {
        const index = rounds.indexOf(round);
        const won = round.leaders.filter((leader) =>
          ["won", "elected", "advanced"].includes(leader.outcome ?? ""),
        );
        return {
          id: round.id,
          kind: round.kind,
          date: round.date,
          label:
            round.kind === "primary"
              ? m.round_primary()
              : round.kind === "runoff"
                ? m.round_runoff()
                : round.kind === "indirect" &&
                    !rounds.some((other) => other.kind === "runoff")
                  ? rounds.length === 2
                    ? index === 0
                      ? m.round_electoral()
                      : m.round_indirect()
                    : m.round_ballot({ round: index + 1 })
                  : round.kind === "indirect"
                    ? m.round_first()
                    : round.kind === "general"
                      ? generals.length < 2
                        ? m.round_general()
                        : generals.indexOf(round) === 0
                          ? m.round_first()
                          : generals.indexOf(round) === 1
                            ? m.round_second()
                            : m.round_number({
                                round: generals.indexOf(round) + 1,
                              })
                      : m.round_number({ round: index + 1 }),
          when: span(round.date, null),
          path: round.path,
          isCurrent: round.isCurrent,
          tone: null,
          leaders: won.length > 0 ? won : round.leaders.slice(0, 1),
          local: null,
        };
      });
    const general = cycle.general;
    const later = [
      ...chained.filter((stage) => stage.kind !== "primary"),
      ...(general == null || rounds.some((round) => round.id === general.id)
        ? []
        : [
            {
              id: general.id,
              kind: "general",
              date: general.date,
              label: m.round_general(),
              when: span(general.date, null),
              path: general.path,
              isCurrent: general.isCurrent,
              tone: null,
              leaders: decided(general.winners),
              local: general.local,
            },
          ]),
    ].toSorted((a, b) => (a.date ?? "").localeCompare(b.date ?? ""));
    const first = [
      ...parties,
      ...chained.filter((stage) => stage.kind === "primary"),
    ];
    return [
      ...(first.length > 0 ? [first] : []),
      ...later.map((stage) => [stage]),
    ];
  });
  const stages = $derived(columns.flat().length);
</script>

{#if stages > 1}
  <nav
    class="col-span-12 grid gap-2.5 bg-card px-3.5 py-3"
    aria-labelledby="{uid}-title"
  >
    <h2 id="{uid}-title" class="sr-only">{m.rounds_title()}</h2>
    <ol class="grid gap-px bg-input p-px sm:auto-cols-fr sm:grid-flow-col">
      {#each columns as column, at (column[0]?.id ?? at)}
        {const rows = $derived(column.length > 1)}
        <li
          class={cn("grid gap-px", {
            "sm:grid-cols-[max-content_minmax(0,1fr)]": rows,
          })}
        >
          {#each column as stage, at (stage.id)}
            {const bar = $derived(
              stage.leaders[0] == null
                ? stage.tone
                : swatch(stage.leaders[0].color, stage.leaders[0].key),
            )}
            <div
              class={cn(
                "relative grid content-start gap-1.5 bg-card px-3 pbs-2 pbe-3",
                {
                  "sm:col-span-2 sm:grid-cols-subgrid sm:items-baseline sm:gap-x-5":
                    rows,
                },
                {
                  "z-10 outline outline-foreground": stage.isCurrent,
                  "hover:z-10 hover:outline hover:outline-foreground":
                    !stage.isCurrent && stage.path != null,
                },
              )}
              aria-current={stage.isCurrent ? "step" : undefined}
            >
              <p
                class={cn(
                  "flex flex-wrap items-baseline gap-x-2 text-[0.8125rem]",
                  { "sm:flex-col sm:gap-y-0.5": rows },
                )}
              >
                {#if stage.isCurrent || stage.path == null}
                  <span
                    class={cn("font-semibold", {
                      "highlighter px-0.5": stage.isCurrent,
                    })}>{stage.label}</span
                  >
                {:else}
                  <a
                    class="font-semibold after:absolute after:inset-0 focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:outline-ring"
                    href={resolve("/[...path]", { path: stage.path })}
                    >{stage.label}</a
                  >
                {/if}
                {#if stage.when !== "" && stage.when !== column[at - 1]?.when}
                  <span class="font-figures text-muted-foreground tabular-nums"
                    >{stage.when}</span
                  >
                {/if}
              </p>
              {#if stage.leaders.length > 0}
                <ul
                  class="grid grid-cols-[minmax(0,max-content)_auto] items-baseline justify-start gap-x-4 gap-y-0.5 text-sm"
                >
                  {#each stage.leaders as leader (leader.key)}
                    <li class="col-span-2 grid grid-cols-subgrid">
                      <Name
                        class="relative z-1 truncate font-medium min-inline-0"
                        label={leader.label}
                        color={swatch(leader.color, leader.key)}
                        person={leader.person}
                        party={leader.party}
                      />
                      <span
                        class="text-end font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                        >{leader.share == null
                          ? ""
                          : percent(leader.share)}</span
                      >
                    </li>
                  {/each}
                </ul>
              {/if}
              {#if stage.local != null && stage.local.place !== ""}
                <p
                  class={cn("relative z-1 text-[0.8125rem]", {
                    "sm:col-start-2": rows,
                  })}
                >
                  {#if stage.local.path == null || stage.local.isCurrent}
                    <span class="font-medium"
                      >{m.cycle_in_place({ place: stage.local.place })}</span
                    >
                  {:else}
                    <Preview
                      class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                      path={stage.local.path}
                      >{m.cycle_in_place({ place: stage.local.place })}</Preview
                    >
                  {/if}
                </p>
              {/if}
              {#if bar != null}
                <span
                  class="absolute inset-x-0 inset-be-0 block-0.75"
                  style:background={bar}
                  aria-hidden="true"
                ></span>
              {/if}
            </div>
          {/each}
        </li>
      {/each}
    </ol>
    {#if cycle.others.length > 0}
      <details class="text-[0.8125rem]">
        <summary
          class="cursor-pointer text-muted-foreground marker:text-border hover:text-foreground"
        >
          {m.cycle_others()}
          <span class="font-figures tabular-nums"
            >{count(cycle.others.length)}</span
          >
        </summary>
        <ul class="mbs-1.5 grid gap-x-6 gap-y-1 xl:grid-cols-2">
          {#each cycle.others as other (other.party.id)}
            <li class="grid gap-x-3 sm:grid-cols-[9.5rem_minmax(0,1fr)]">
              <Name
                class="truncate font-medium"
                label={other.party.label}
                color={other.party.color}
              />
              <span class="flex flex-wrap gap-x-2.5">
                {#each other.races as race, index (index)}
                  {#if race.path == null || race.isCurrent}
                    <span class={cn({ "font-semibold": race.isCurrent })}
                      >{race.place}</span
                    >
                  {:else}
                    <Preview
                      class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                      path={race.path}>{race.place}</Preview
                    >
                  {/if}
                {/each}
              </span>
            </li>
          {/each}
        </ul>
      </details>
    {/if}
  </nav>
{/if}
