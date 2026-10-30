<script lang="ts">
  import { refreshAll } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    states,
    isLive,
    rehearsal,
  }: {
    states: PageData["night"]["states"];
    isLive: boolean;
    rehearsal: string | null;
  } = $props();

  const query = $derived(
    rehearsal == null
      ? ""
      : `?${new URLSearchParams({ rehearsal }).toString()}`,
  );
  const reporting = $derived(
    states.filter((state) => state.status !== "waiting").length,
  );
  const latest = $derived(
    states
      .flatMap((state) => (state.newest == null ? [] : [state.newest]))
      .toSorted((a, b) => a.localeCompare(b))
      .at(-1) ?? null,
  );
  const clocks = $derived(
    new Map(
      [
        ...new Set(["America/New_York", ...states.map((state) => state.zone)]),
      ].map((zone) => [
        zone,
        new Intl.DateTimeFormat(getLocale(), {
          hour: "numeric",
          minute: "2-digit",
          timeZone: zone,
          timeZoneName: "short",
        }),
      ]),
    ),
  );

  $effect(() => {
    if (!isLive) {
      return;
    }
    const timer = setInterval(() => void refreshAll(), 60_000);
    return () => clearInterval(timer);
  });
</script>

<section
  aria-labelledby="live-board"
  class="grid gap-4 bg-card px-3.5 pbs-5 pbe-6 sm:px-6"
>
  <div class="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
    <h2 id="live-board" class="heading">{m.live_landing_board()}</h2>
    <p class="text-[0.8125rem] text-muted-foreground tabular-nums">
      {m.live_landing_summary({
        counting: String(reporting),
        total: String(states.length),
      })}{#if latest != null}. {m.live_landing_latest({
          time:
            clocks.get("America/New_York")?.format(Date.parse(latest)) ?? "",
        })}{/if}
    </p>
  </div>
  <ul
    class="grid grid-cols-[repeat(auto-fill,minmax(2.75rem,1fr))] gap-1 sm:grid-cols-[repeat(auto-fill,minmax(3.25rem,1fr))]"
  >
    {#each states as state (state.code)}
      <li>
        <a
          href={resolve(`/live/${state.path}${query}`)}
          aria-label="{state.name}: {state.status === 'counting'
            ? m.live_landing_legend_counting()
            : state.status === 'certified'
              ? m.live_landing_legend_certified()
              : m.live_landing_legend_waiting()}"
          title={state.name}
          class={cn(
            "relative grid place-items-center overflow-hidden rounded-xs font-figures text-[0.8125rem] font-semibold uppercase tabular-nums block-10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground",
            {
              "bg-muted text-muted-foreground hover:text-foreground":
                state.status === "waiting",
              "bg-card text-foreground ring-1 ring-foreground ring-inset":
                state.status === "counting",
              "bg-foreground text-background": state.status === "certified",
            },
          )}
        >
          {state.code}
          {#if state.status === "counting"}
            <span
              class="absolute inset-s-0 inset-be-0 bg-(--no) transition-[inline-size] duration-700 ease-out block-1 motion-reduce:transition-none"
              style:inline-size="{Math.max(0.06, state.reported ?? 0) * 100}%"
            ></span>
          {/if}
        </a>
      </li>
    {/each}
  </ul>
  <ul
    class="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground"
    aria-hidden="true"
  >
    <li class="flex items-center gap-1.5">
      <span class="rounded-xs bg-muted block-3 inline-3"></span>
      {m.live_landing_legend_waiting()}
    </li>
    <li class="flex items-center gap-1.5">
      <span
        class="relative overflow-hidden rounded-xs ring-1 ring-foreground block-3 inline-3 ring-inset"
        ><span
          class="absolute inset-s-0 inset-be-0 bg-(--no) block-0.5 inline-2"
        ></span></span
      >
      {m.live_landing_legend_counting()}
    </li>
    <li class="flex items-center gap-1.5">
      <span class="rounded-xs bg-foreground block-3 inline-3"></span>
      {m.live_landing_legend_certified()}
    </li>
  </ul>
</section>

<section class="bg-card px-3.5 pbs-2 pbe-8 sm:px-6">
  <table class="text-[0.875rem] inline-full">
    <thead>
      <tr
        class="border-be border-border text-start text-xs text-muted-foreground"
      >
        <th
          scope="col"
          class="py-2 pe-3 text-start font-medium whitespace-nowrap"
          >{m.live_col_state()}</th
        >
        <th
          scope="col"
          class="py-2 pe-3 text-start font-medium whitespace-nowrap"
          >{m.live_reporting()}</th
        >
        <th scope="col" class="py-2 pe-3 text-end font-medium max-sm:hidden"
          >{m.live_col_races()}</th
        >
        <th
          scope="col"
          class="py-2 text-end font-medium whitespace-nowrap sm:pe-3"
          >{m.live_col_updated()}</th
        >
        <th scope="col" class="py-2 max-sm:hidden"
          ><span class="sr-only">{m.live_ballot()}</span></th
        >
      </tr>
    </thead>
    <tbody>
      {#each states as state (state.code)}
        <tr class="border-be border-border align-middle">
          <th scope="row" class="py-2.5 pe-3 text-start font-normal">
            <a
              href={resolve(`/live/${state.path}${query}`)}
              class="font-medium hover:underline">{state.name}</a
            >
            <span
              class="block text-xs text-muted-foreground tabular-nums sm:hidden"
              >{m.live_races_of({
                counting: String(state.counting),
                total: String(state.contests),
              })}</span
            >
          </th>
          <td class="py-2.5 pe-3">
            {#if state.status === "counting"}
              <span class="flex items-center gap-2.5">
                <span
                  class="relative shrink-0 overflow-hidden rounded-xs bg-muted block-1.5 inline-16 sm:inline-28"
                >
                  <span
                    class="absolute inset-s-0 inset-bs-0 inset-be-0 bg-foreground transition-[inline-size] duration-700 ease-out motion-reduce:transition-none"
                    style:inline-size="{(state.reported ?? 0) * 100}%"
                  ></span>
                </span>
                <span class="font-figures font-semibold tabular-nums"
                  >{state.reported == null
                    ? m.live_landing_legend_counting()
                    : percent(state.reported)}</span
                >
              </span>
            {:else if state.status === "certified"}
              <span class="font-medium"
                >{m.live_landing_legend_certified()}</span
              >
            {:else}
              <span class="whitespace-nowrap text-muted-foreground"
                >{m.live_landing_legend_waiting()}</span
              >
            {/if}
          </td>
          <td
            class="py-2.5 pe-3 text-end font-figures text-muted-foreground tabular-nums max-sm:hidden"
          >
            {m.live_races_of({
              counting: String(state.counting),
              total: String(state.contests),
            })}
          </td>
          <td
            class="py-2.5 text-end font-figures whitespace-nowrap tabular-nums sm:pe-3"
          >
            {#if state.newest != null}
              {clocks.get(state.zone)?.format(Date.parse(state.newest))}
            {/if}
          </td>
          <td class="py-2.5 text-end max-sm:hidden">
            <a
              href={resolve(`/${state.path}`)}
              class="text-[0.8125rem] text-muted-foreground hover:text-foreground hover:underline"
              >{m.live_ballot()}</a
            >
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</section>
