<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import ChevronsUpDownIcon from "@lucide/svelte/icons/chevrons-up-down";
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import { on } from "svelte/events";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { count, date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import ElectionRow from "./election-row.svelte";

  const {
    series,
  }: { series: Extract<PageData, { kind: "contest" }>["contest"]["series"] } =
    $props();

  const carried = $derived.by(() => {
    const query = new URLSearchParams(
      [...page.url.searchParams].filter(([key]) => key !== "vs"),
    )
      .toString()
      .replaceAll("%2F", "/");
    return query === "" ? ("" as const) : (`?${query}` as const);
  });
  const years = $derived.by(() => {
    const seen = Object.groupBy(
      series,
      (entry) => entry.year?.toString() ?? "",
    );
    const month = new Intl.DateTimeFormat(getLocale(), {
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    });
    return series.map((entry) => {
      const year = entry.year?.toString() ?? "";
      return {
        ...entry,
        year,
        label:
          (seen[year]?.length ?? 0) > 1 && entry.date != null
            ? month.format(new Date(`${entry.date}T00:00:00Z`))
            : year,
        color:
          entry.winner == null
            ? null
            : swatch(entry.winner.color, entry.winner.key),
        href:
          entry.path == null
            ? null
            : resolve(`/[...path]${carried}`, { path: entry.path }),
      };
    });
  });
  const at = $derived(years.findIndex((entry) => entry.current));
  const previous = $derived(
    at < 1 ? null : years.slice(0, at).findLast((entry) => entry.href != null),
  );
  const next = $derived(
    at < 0 ? null : years.slice(at + 1).find((entry) => entry.href != null),
  );
  const items = $derived.by(() => {
    const day = new Intl.DateTimeFormat(getLocale(), {
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
    const dated = new Intl.DateTimeFormat(getLocale(), {
      dateStyle: "medium",
      timeZone: "UTC",
    });
    return years
      .toReversed()
      .flatMap((entry) =>
        entry.path == null
          ? []
          : [
              {
                value: entry.path,
                label: entry.name,
                year: entry.year,
                winner: entry.winner,
                when:
                  entry.date == null
                    ? ""
                    : (entry.date.startsWith(entry.year) ? day : dated).format(
                        new Date(`${entry.date}T00:00:00Z`),
                      ),
                annulled: entry.annulled,
                keywords: [entry.date ?? "", entry.winner?.label ?? ""],
              },
            ],
      );
  });

  const tether = TooltipPrimitive.createTether<number>();

  let centered = false;
</script>

<svelte:window
  onkeydown={(event: KeyboardEvent) => {
    if (
      event.defaultPrevented ||
      event.metaKey ||
      event.ctrlKey ||
      event.altKey ||
      (event.target instanceof HTMLElement &&
        (event.target.isContentEditable ||
          event.target.closest(
            "input, textarea, select, [role='dialog'], [role='listbox']",
          ) != null))
    ) {
      return;
    }
    const path =
      event.key === "["
        ? previous?.path
        : event.key === "]"
          ? next?.path
          : null;
    if (path != null) {
      event.preventDefault();
      void goto(resolve(`/[...path]${carried}`, { path }));
    }
  }}
/>

{#snippet winner(entry: (typeof years)[number])}
  {#if entry.winner != null}
    <span class="flex items-baseline gap-1.5">
      <span
        class="shrink-0 self-center rounded-xs block-2 inline-2"
        style:background-color={entry.color}
      ></span>
      <span class="flex-1">{entry.winner.label}</span>
      <span class="font-mono text-xs tabular-nums opacity-70"
        >{entry.winner.measure === "electoral_votes"
          ? m.series_electoral({
              votes: count(entry.winner.votes),
              total: count(entry.winner.total),
            })
          : percent(entry.winner.share)}</span
      >
    </span>
  {/if}
{/snippet}

{#snippet about(entry: (typeof years)[number] | undefined)}
  {#if entry != null}
    <Tooltip.Content
      collisionPadding={8}
      class="grid gap-1 leading-snug inline-[min(18rem,calc(100vw-2rem))]"
    >
      <span class="flex items-baseline justify-between gap-3">
        <span class="font-semibold">{entry.name}</span>
        {#if entry.annulled}
          <span class="shrink-0 text-xs font-semibold"
            >{m.election_annulled()}</span
          >
        {/if}
      </span>
      {#if entry.date != null}
        <span class="font-mono text-xs opacity-70">{date(entry.date)}</span>
      {/if}
      {@render winner(entry)}
    </Tooltip.Content>
  {/if}
{/snippet}

{#snippet step(
  entry: (typeof years)[number] | null | undefined,
  key: string,
  text: (input: { label: string }) => string,
  Icon: typeof ChevronLeftIcon,
)}
  {#if entry?.href == null}
    <span
      class="flex shrink-0 items-center justify-center rounded-sm border border-input bg-card text-muted-foreground opacity-40 block-8 inline-8 max-sm:hidden"
      aria-hidden="true"><Icon class="block-4 inline-4" /></span
    >
  {:else}
    <Tooltip.Root>
      <Tooltip.Trigger>
        {#snippet child({ props })}
          <a
            {...props}
            type={undefined}
            class="flex shrink-0 keycap items-center justify-center rounded-sm border border-input bg-card block-8 inline-8 hover:bg-secondary max-sm:hidden"
            href={entry.href}
            aria-label={text({ label: entry.label })}
            aria-keyshortcuts={key}><Icon class="block-4 inline-4" /></a
          >
        {/snippet}
      </Tooltip.Trigger>
      <Tooltip.Content
        collisionPadding={8}
        class="grid gap-1 leading-snug inline-[min(18rem,calc(100vw-2rem))]"
      >
        <span class="flex items-baseline justify-between gap-3">
          <span class="font-semibold">{text({ label: entry.label })}</span>
          <kbd
            class="rounded-xs border border-current/30 px-1 font-mono text-[0.6875rem]"
            >{key}</kbd
          >
        </span>
        {@render winner(entry)}
      </Tooltip.Content>
    </Tooltip.Root>
  {/if}
{/snippet}

<nav
  class={cn("flex items-stretch gap-1.5 min-inline-0", {
    "md:col-span-2": years.length > 8,
  })}
  aria-label={m.series_label()}
>
  {@render step(previous, "[", m.series_previous, ChevronLeftIcon)}
  <div
    class="flex flex-1 scrollbar-none flex-row-reverse overflow-x-auto min-inline-0"
    {@attach (strip: HTMLDivElement) => {
      const index = at;
      const fade = () => {
        const box = strip.getBoundingClientRect();
        const first = strip.querySelector("li")?.getBoundingClientRect();
        const last = strip
          .querySelector("li:last-child")
          ?.getBoundingClientRect();
        const start = first != null && box.left - first.left > 1;
        const end = last != null && last.right - box.right > 1;
        strip.style.maskImage =
          start || end
            ? `linear-gradient(to right, ${start ? "transparent, black 2.5rem" : "black"}, ${end ? "black calc(100% - 2.5rem), transparent" : "black"})`
            : "";
      };
      void document.fonts.ready.then(() => {
        const current = strip.querySelectorAll("li")[index];
        if (current != null) {
          const box = current.getBoundingClientRect();
          strip.scrollBy({
            left:
              box.left -
              strip.getBoundingClientRect().left -
              (strip.clientWidth - box.width) / 2,
            behavior:
              centered &&
              !matchMedia("(prefers-reduced-motion: reduce)").matches
                ? "smooth"
                : "instant",
          });
          centered = true;
        }
        fade();
      });
      const observer = new ResizeObserver(fade);
      observer.observe(strip);
      const off = on(strip, "scroll", fade, { passive: true });
      return () => {
        observer.disconnect();
        off();
      };
    }}
  >
    <Tooltip.Root {tether} delayDuration={250}>
      {#snippet children({ payload })}
        <ol class="me-auto flex">
          {#each years as entry, index (entry.path ?? entry.label)}
            <li class="-ms-px first:ms-0">
              <Tooltip.Trigger {tether} payload={index}>
                {#snippet child({ props })}
                  <svelte:element
                    this={entry.current || entry.href == null ? "span" : "a"}
                    {...props}
                    type={undefined}
                    class={cn(
                      "relative flex items-center border border-input bg-card px-2.5 font-mono text-[0.8125rem] tabular-nums block-8",
                      {
                        "z-10 border-foreground highlighter font-semibold":
                          entry.current,
                        "text-muted-foreground hover:z-10 hover:border-foreground hover:text-foreground":
                          !entry.current && entry.href != null,
                      },
                      { "line-through": entry.annulled },
                    )}
                    href={entry.current ? undefined : entry.href}
                    aria-current={entry.current ? "page" : undefined}
                    aria-label={entry.annulled
                      ? m.series_annulled({ year: entry.label })
                      : undefined}
                    >{entry.label}{#if entry.color != null}<span
                        class="absolute inset-x-0 inset-be-0 bg-(--party) block-0.75"
                        style:--party={entry.color}
                        aria-hidden="true"
                      ></span>{/if}</svelte:element
                  >
                {/snippet}
              </Tooltip.Trigger>
            </li>
          {/each}
        </ol>
        {@render about(payload == null ? undefined : years.at(Number(payload)))}
      {/snippet}
    </Tooltip.Root>
  </div>
  {@render step(next, "]", m.series_next, ChevronRightIcon)}
  <Combobox
    wide
    {items}
    value={years[at]?.path ?? null}
    label={m.series_all_label({ count: items.length })}
    placeholder=""
    search={m.series_filter()}
    empty={m.series_empty()}
    onValueChange={(value) => {
      if (value != null && value !== years[at]?.path) {
        void goto(resolve(`/[...path]${carried}`, { path: value }));
      }
    }}
  >
    {#snippet trigger(props: Record<string, unknown>)}
      <button
        {...props}
        type="button"
        class="flex shrink-0 keycap items-center gap-1.5 rounded-sm border border-input bg-card ps-2.5 pe-2 text-[0.8125rem] font-semibold whitespace-nowrap block-8 hover:bg-secondary"
        aria-label={m.series_all_label({ count: items.length })}
        >{m.series_all({ count: items.length })}<ChevronsUpDownIcon
          class="text-muted-foreground block-3.5 inline-3.5"
        /></button
      >
    {/snippet}
    {#snippet row(item: (typeof items)[number])}
      <ElectionRow
        year={item.year}
        winner={item.winner}
        phrase={item.label}
        when={item.when}
        annulled={item.annulled}
      />
    {/snippet}
  </Combobox>
</nav>
