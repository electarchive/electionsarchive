<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { navigating, page } from "$app/state";
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { count as figure, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    count,
    here,
    table,
    isDerived,
  }: {
    count: NonNullable<
      Extract<PageData, { kind: "contest" }>["contest"]["count"]
    >;
    here: string;
    table: string;
    isDerived: boolean;
  } = $props();

  const uid = $props.id();

  let pending = $state<{ round: number; href: string } | null>(null);

  const href = (round: number) => {
    const kept = [...page.url.searchParams].filter(([key]) => key !== "round");
    const query = new URLSearchParams(
      round === count.initial ? kept : [...kept, ["round", String(round)]],
    );
    const text = query.toString().replaceAll("%2F", "/");
    const suffix = text === "" ? ("" as const) : (`?${text}` as const);
    return resolve(`/[...path]${suffix}`, { path: here });
  };
  const shown = $derived(
    pending != null &&
      navigating.to != null &&
      `${navigating.to.url.pathname}${navigating.to.url.search}` ===
        pending.href
      ? pending.round
      : count.round,
  );
  const rounds = $derived(
    Array.from({ length: count.last }, (_, step) => {
      let total = 0;
      for (const row of count.rows) {
        total += row.votes[step] ?? 0;
      }
      return {
        round: step + 1,
        rows: count.rows
          .filter((row) => (row.votes[step] ?? 0) > 0)
          .map((row) => ({
            key: row.key,
            label: row.label,
            color: swatch(row.color, row.key),
            share: total === 0 ? 0 : (row.votes[step] ?? 0) / total,
          })),
      };
    }),
  );
  const standing = $derived(
    count.rows.filter((row) => row.votes[shown - 1] != null),
  );
  const leaving = $derived(
    shown === count.last
      ? []
      : standing.filter((row) => row.votes[shown] == null),
  );
  const list = $derived(
    new Intl.ListFormat(getLocale(), { style: "long", type: "conjunction" }),
  );
  const named = (keys: Array<string>) =>
    list.format(
      keys.map((key) => count.rows.find((row) => row.key === key)?.label ?? ""),
    );
  const action = $derived.by(() => {
    const step = count.stv?.steps[shown - 1];
    if (step == null) {
      return null;
    }
    return {
      text:
        step.kind === "first"
          ? m.stv_first()
          : step.kind === "surplus"
            ? m.stv_surplus({ names: named(step.from) })
            : step.kind === "exclusion"
              ? m.stv_exclusion({ names: named(step.from) })
              : m.stv_none(),
      elected: step.elected.length === 0 ? null : named(step.elected),
    };
  });
  const out = $derived.by(() => {
    const names = leaving.map((row) => row.label);
    const more = m.count_more({ count: figure(names.length - 3) });
    return list
      .formatToParts(names.length > 4 ? [...names.slice(0, 3), more] : names)
      .map((part) => ({
        ...part,
        isMore: names.length > 4 && part.value === more,
      }));
  });
  const name = (round: number) =>
    count.stv != null
      ? m.stv_count({ count: round })
      : round === count.last
        ? m.count_final()
        : m.count_round({ round });

  const go = (round: number) => {
    const target = href(round);
    pending = { round, href: target };
    return target;
  };
</script>

{#snippet step(round: number, label: string, Icon: typeof ChevronLeftIcon)}
  {#if round < 1 || round > count.last}
    <span
      class="grid place-items-center rounded-sm text-muted-foreground/40 block-7 inline-7 pointer-coarse:block-9 pointer-coarse:inline-9"
      aria-hidden="true"><Icon class="block-4 inline-4" /></span
    >
  {:else}
    <a
      class="grid place-items-center rounded-sm text-muted-foreground keycap block-7 inline-7 hover:bg-secondary hover:text-foreground pointer-coarse:block-9 pointer-coarse:inline-9"
      href={href(round)}
      aria-label={label}
      data-sveltekit-reset="false"
      onclick={() => go(round)}><Icon class="block-4 inline-4" /></a
    >
  {/if}
{/snippet}

<section
  class="mbe-1 grid gap-2 border-be px-2 pbs-1 pbe-3"
  aria-labelledby="{uid}-title"
>
  <div class="flex items-center justify-between gap-3">
    <h2 id="{uid}-title" class="text-sm font-semibold">
      {count.stv != null
        ? m.count_title_stv()
        : count.isSupplementary
          ? m.count_title_supplementary()
          : m.count_title()}
    </h2>
    <div class="-me-1.5 flex items-center">
      {@render step(shown - 1, m.count_previous(), ChevronLeftIcon)}
      {@render step(shown + 1, m.count_next(), ChevronRightIcon)}
    </div>
  </div>
  <ol
    class="-mx-1 grid auto-cols-[minmax(1.75rem,1fr)] grid-flow-col overflow-x-auto"
    {@attach (node: HTMLElement) => {
      const cell = node.children[shown - 1];
      if (cell instanceof HTMLElement) {
        node.scrollLeft = Math.max(
          0,
          cell.offsetLeft + cell.offsetWidth - node.clientWidth,
        );
      }
    }}
  >
    {#each rounds as entry (entry.round)}
      {const isShown = $derived(entry.round === shown)}
      <li class="flex">
        <a
          class={cn(
            "group relative grid flex-1 content-end gap-1.5 rounded-xs px-1 pbs-2 pbe-1.5 keycap pointer-coarse:pbs-3",
            {
              "bg-secondary text-foreground": isShown,
              "text-muted-foreground hover:bg-secondary/60 hover:text-foreground":
                !isShown,
            },
          )}
          href={href(entry.round)}
          aria-current={isShown ? "step" : undefined}
          aria-label={name(entry.round)}
          tabindex={isShown ? 0 : -1}
          data-sveltekit-reset="false"
          onclick={() => go(entry.round)}
          onkeydown={(event) => {
            const next =
              event.key === "ArrowLeft"
                ? shown - 1
                : event.key === "ArrowRight"
                  ? shown + 1
                  : event.key === "Home"
                    ? 1
                    : event.key === "End"
                      ? count.last
                      : null;
            if (
              next == null ||
              next < 1 ||
              next > count.last ||
              next === shown
            ) {
              return;
            }
            event.preventDefault();
            const links = event.currentTarget
              .closest("ol")
              ?.querySelectorAll("a");
            links?.[next - 1]?.focus();
            void goto(go(next), { reset: false }).catch(() => null);
          }}
        >
          <span
            class={cn("flex gap-px overflow-hidden rounded-[1px]", {
              "block-2": isShown,
              "opacity-70 block-1 group-hover:opacity-100": !isShown,
            })}
            aria-hidden="true"
          >
            {#each entry.rows as row (row.key)}
              <span
                class="bg-(--party) min-inline-px"
                style:--party={row.color}
                style:flex-grow={row.share}
              ></span>
            {/each}
          </span>
          <span
            class={cn(
              "truncate text-center font-figures text-xs leading-none tabular-nums",
              { "font-semibold": isShown },
            )}
            aria-hidden="true"
            >{count.last > 5 ? entry.round : name(entry.round)}</span
          >
          {#if isShown}
            <span
              class={cn(
                "absolute inset-x-1 -inset-be-px bg-foreground block-0.5",
                {
                  "animate-pulse motion-reduce:animate-none":
                    shown !== count.round,
                },
              )}
              aria-hidden="true"
            ></span>
          {/if}
        </a>
      </li>
    {/each}
  </ol>
  <p
    class="text-[0.8125rem] text-pretty text-muted-foreground"
    aria-live="polite"
  >
    {#if action == null}
      {#if count.last > 5}
        <span class="font-semibold text-foreground"
          >{shown === count.last
            ? m.count_final()
            : m.count_of({ round: shown, last: count.last })}.</span
        >
      {/if}
      {m.count_continuing({ count: standing.length })}.
      {#if leaving.length > 0}
        {const frame = $derived(
          m.count_eliminated({ names: "\u0000" }).split("\u0000"),
        )}
        <span class="text-foreground"
          >{frame[0]}{#each out as part, index (index)}{#if part.isMore}<a
                class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                href="#{table}">{part.value}</a
              >{:else}{part.value}{/if}{/each}{frame[1]}.</span
        >
      {/if}
    {:else}
      {m.stv_step({ count: shown, last: count.last, action: action.text })}
      {#if action.elected != null}
        <span class="text-foreground"
          >{m.stv_step_elected({ names: action.elected })}</span
        >
      {/if}
    {/if}
  </p>
  {#if isDerived}
    <p class="text-xs text-pretty text-muted-foreground">
      {m.count_derived()}
      <a
        class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
        href="#notes">{m.count_derived_link()}</a
      >
    </p>
  {/if}
  <p class="sr-only">
    {#each rounds[shown - 1]?.rows ?? [] as row (row.key)}{row.label}
      {percent(row.share)}.
    {/each}
  </p>
</section>
