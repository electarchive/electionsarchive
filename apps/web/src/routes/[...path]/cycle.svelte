<script lang="ts">
  import { resolve } from "$app/paths";
  import Name from "#lib/components/name.svelte";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";
  import Preview from "./preview.svelte";

  type Winner = {
    label: string;
    person: string | null;
    party: string | null;
    color: string | null;
  };

  const {
    primaries,
    general,
    others,
    othersTitle,
    day = null,
  }: {
    primaries: Array<{
      key: string;
      label: string;
      color: string | null;
      path: string | null;
      date: string | null;
      until: string | null;
      isCurrent: boolean;
      winners: Array<Winner>;
      runoff: {
        path: string | null;
        date: string | null;
        isCurrent: boolean;
        winners: Array<Winner>;
      } | null;
      local: { path: string | null; place: string; isCurrent: boolean } | null;
    }>;
    general: {
      path: string | null;
      date: string | null;
      isCurrent: boolean;
      winners: Array<Winner>;
      local: { path: string | null; place: string; isCurrent: boolean } | null;
    } | null;
    others: Array<{
      key: string;
      label: string;
      color: string | null;
      races: Array<{ label: string; path: string | null; isCurrent: boolean }>;
    }>;
    othersTitle: string;
    day?: {
      date: string | null;
      part: { name: string; path: string | null } | null;
      groups: Array<{
        key: string;
        label: string;
        count: number;
        path: string;
        anchor: string | null;
      }>;
    } | null;
  } = $props();

  const uid = $props.id();

  const span = (from: string | null, to: string | null) => {
    if (from == null) {
      return "";
    }
    const format = new Intl.DateTimeFormat(getLocale(), {
      dateStyle: "medium",
      timeZone: "UTC",
    });
    const start = new Date(`${from}T00:00:00Z`);
    return to == null
      ? format.format(start)
      : format.formatRange(start, new Date(`${to}T00:00:00Z`));
  };

  const isRoad = $derived(
    primaries.length > 0 ||
      others.length > 0 ||
      (general != null && !general.isCurrent),
  );
  const isDay = $derived(
    day != null && (day.groups.length > 0 || day.part != null),
  );
</script>

{#snippet link(path: string | null, text: string, isCurrent: boolean)}
  {#if path == null || isCurrent}
    <span
      class={cn("font-semibold", { "bg-marker/45 px-0.5": isCurrent })}
      aria-current={isCurrent ? "page" : undefined}>{text}</span
    >
  {:else}
    <Preview
      class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
      {path}>{text}</Preview
    >
  {/if}
{/snippet}

{#snippet winners(label: string, rows: Array<Winner>)}
  {#if rows.length > 0}
    <p class="flex flex-wrap items-baseline gap-x-2 text-[0.8125rem]">
      <span class="text-muted-foreground">{label}</span>
      {#each rows as row, index (index)}
        <Name
          class="font-medium"
          label={row.label}
          color={row.color}
          person={row.person}
          party={row.party}
        />
      {/each}
    </p>
  {/if}
{/snippet}

{#snippet local(
  entry: { path: string | null; place: string; isCurrent: boolean } | null,
)}
  {#if entry != null && entry.place !== ""}
    <p class="text-[0.8125rem]">
      {@render link(
        entry.path,
        m.cycle_in_place({ place: entry.place }),
        entry.isCurrent,
      )}
    </p>
  {/if}
{/snippet}

{#if isRoad || isDay}
  <nav
    class={cn(
      "col-span-12 grid gap-x-10 gap-y-5 bg-card px-3.5 pbs-3 pbe-3.5",
      { "lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]": isRoad && isDay },
    )}
    aria-label={m.cycle_title()}
  >
    {#if isRoad}
      <section class="grid content-start gap-2" aria-labelledby="{uid}-road">
        <h2 id="{uid}-road" class="text-[0.8125rem] font-bold">
          {m.cycle_road()}
        </h2>
        <ol
          class="grid gap-x-3 gap-y-3.5 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]"
        >
          <li
            class="grid content-start gap-2.5 border-bs-2 border-border pbs-2"
          >
            {#each primaries as primary (primary.key)}
              <div
                class="grid gap-0.5 border-s-[3px] border-border ps-2"
                style:border-inline-start-color={primary.color}
              >
                <p class="flex flex-wrap items-baseline gap-x-2 text-sm">
                  {@render link(primary.path, primary.label, primary.isCurrent)}
                  <span
                    class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                    >{span(primary.date, primary.until)}</span
                  >
                </p>
                {@render winners(m.cycle_nominee(), primary.winners)}
                {#if primary.runoff != null}
                  <p class="text-[0.8125rem]">
                    {@render link(
                      primary.runoff.path,
                      m.cycle_runoff({ date: date(primary.runoff.date) }),
                      primary.runoff.isCurrent,
                    )}
                  </p>
                  {@render winners(m.cycle_nominee(), primary.runoff.winners)}
                {/if}
                {@render local(primary.local)}
              </div>
            {/each}
            {#if others.length > 0}
              <details class="group text-[0.8125rem]">
                <summary
                  class="cursor-pointer text-muted-foreground marker:text-border hover:text-foreground"
                >
                  {othersTitle}
                  <span class="font-figures tabular-nums"
                    >{count(others.length)}</span
                  >
                </summary>
                <ul class="mbs-1.5 grid gap-x-6 gap-y-1 xl:grid-cols-2">
                  {#each others as other (other.key)}
                    <li
                      class="grid gap-x-3 sm:grid-cols-[9.5rem_minmax(0,1fr)]"
                    >
                      <Name
                        class="truncate font-medium"
                        label={other.label}
                        color={other.color}
                      />
                      <span class="flex flex-wrap gap-x-2.5">
                        {#each other.races as race, index (index)}
                          {#if race.path == null || race.isCurrent}
                            <span
                              class={cn({ "font-semibold": race.isCurrent })}
                              >{race.label}</span
                            >
                          {:else}
                            <Preview
                              class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                              path={race.path}>{race.label}</Preview
                            >
                          {/if}
                        {/each}
                      </span>
                    </li>
                  {/each}
                </ul>
              </details>
            {/if}
          </li>
          {#if general != null}
            <li
              class={cn("grid content-start gap-0.5 border-bs-2 pbs-2", {
                "border-foreground": general.isCurrent,
                "border-border": !general.isCurrent,
              })}
            >
              <p class="flex flex-wrap items-baseline gap-x-2 text-sm">
                {@render link(
                  general.path,
                  m.cycle_general(),
                  general.isCurrent,
                )}
                <time
                  class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                  datetime={general.date}>{span(general.date, null)}</time
                >
              </p>
              {@render winners(m.cycle_winner(), general.winners)}
              {@render local(general.local)}
            </li>
          {/if}
        </ol>
      </section>
    {/if}
    {#if day != null && isDay}
      <section class="grid content-start gap-2" aria-labelledby="{uid}-day">
        <h2 id="{uid}-day" class="text-[0.8125rem] font-bold">
          {m.cycle_day({ date: date(day.date) })}
        </h2>
        <ul
          class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,11.5rem),1fr))] gap-x-4 gap-y-1.5 border-bs-2 border-border pbs-2"
        >
          {#if day.part?.path != null}
            <li class="col-span-full text-sm">
              <Preview
                class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
                path={day.part.path}>{day.part.name}</Preview
              >
            </li>
          {/if}
          {#each day.groups as group (group.key)}
            <li>
              <a
                class="group grid grid-cols-[3.25rem_minmax(0,1fr)] items-baseline gap-x-2 text-[0.8125rem]/snug"
                href="{resolve('/[...path]', {
                  path: group.path,
                })}{group.anchor == null ? '' : `#${group.anchor}`}"
              >
                <span
                  class="text-end font-figures text-[0.9375rem] font-semibold tabular-nums"
                  >{group.count > 1 ? count(group.count) : ""}</span
                >
                <span
                  class="underline decoration-border underline-offset-4 group-hover:decoration-foreground"
                  >{group.label}</span
                >
              </a>
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  </nav>
{/if}
