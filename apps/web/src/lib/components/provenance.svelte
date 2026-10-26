<script lang="ts">
  import ArrowUpRightIcon from "@lucide/svelte/icons/arrow-up-right";
  import XIcon from "@lucide/svelte/icons/x";
  import { createQuery } from "@tanstack/svelte-query";
  import { MediaQuery } from "svelte/reactivity";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { count, date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { provenance } from "#lib/provenance.svelte.js";
  import { orpc } from "#lib/rpc.js";

  const STAGES: Record<string, () => string> = {
    live: m.stage_live,
    preliminary: m.stage_preliminary,
    official: m.stage_official,
    certified: m.stage_certified,
    court_revised: m.stage_court_revised,
    recount: m.stage_recount,
    alternative: m.stage_alternative,
  };

  const wide = new MediaQuery("min-width: 640px");
  let sources = $state<Array<string>>([]);

  const value = $derived(provenance.value);
  const trace = createQuery(() => ({
    ...orpc.contests.trace.queryOptions({
      input: {
        path: value?.path ?? "",
        key: value?.key ?? null,
        area: value?.area ?? null,
        place: value?.place ?? null,
      },
    }),
    enabled: provenance.isOpen && value?.path != null,
    staleTime: Infinity,
    meta: { quiet: true },
  }));
  const note = $derived(
    value == null
      ? null
      : {
          share: m.provenance_share_note(),
          votes: m.term_valid(),
          margin: m.term_margin(),
          swing: m.method_swing(),
          seats: m.method_seats(),
        }[value.measure],
  );

  $effect(() => {
    if (provenance.isOpen && provenance.value == null) {
      sources = [
        ...new Set(
          [...document.querySelectorAll<HTMLElement>("[data-source]")].flatMap(
            (element) =>
              (
                element.dataset.source ??
                element.textContent.replaceAll(/\s+/g, " ").trim()
              ).split("; "),
          ),
        ),
      ].filter((source) => source !== "");
    }
  });

  function restore(event: Event) {
    event.preventDefault();
    provenance.anchor?.focus({ preventScroll: true });
  }
</script>

{#snippet body()}
  <div class="grid gap-4 p-4">
    <div class="flex items-start justify-between gap-3">
      {#if value == null}
        <h2 class="text-base font-semibold">{m.provenance_title()}</h2>
      {:else}
        <div class="grid gap-0.5 min-inline-0">
          <p class="text-[0.8125rem] text-pretty text-muted-foreground">
            {value.label}
          </p>
          <p
            class="font-figures text-2xl/none font-[760] tracking-[-0.015em] tabular-nums"
          >
            {value.figure}
          </p>
        </div>
      {/if}
      <button
        type="button"
        class="-me-2 -mbs-1.5 grid shrink-0 place-items-center rounded-sm text-muted-foreground block-8 inline-8 hover:bg-secondary hover:text-foreground"
        aria-label={m.map_sheet_close()}
        onclick={() => (provenance.isOpen = false)}
      >
        <XIcon class="block-4 inline-4" />
      </button>
    </div>

    {#if value != null && (value.math != null || note != null)}
      <section class="grid gap-1.5">
        <h3 class="text-xs font-semibold text-muted-foreground">
          {m.provenance_calculation()}
        </h3>
        {#if value.math != null}
          <p class="font-figures text-sm tabular-nums">
            {#if "votes" in value.math}
              {count(value.math.votes)} ÷ {m.provenance_valid_votes({
                valid: count(value.math.valid),
              })} =
            {:else}
              {percent(value.math.first)} − {percent(value.math.second)} =
            {/if}
            <span class="font-semibold">{value.figure}</span>
          </p>
        {/if}
        {#if note != null}
          <p class="text-[0.8125rem] text-pretty text-muted-foreground">
            {note}
          </p>
        {/if}
      </section>
    {/if}

    {#if value?.path != null}
      <section class="grid gap-2">
        <h3 class="text-xs font-semibold text-muted-foreground">
          {m.provenance_source()}
        </h3>
        {#if trace.isPending}
          <p class="text-[0.8125rem] text-muted-foreground">
            {m.provenance_loading()}
          </p>
        {:else if trace.data != null}
          <ul class="grid gap-3">
            {#each trace.data.trail as entry (`${entry.changeset}:${entry.stage}:${entry.title}`)}
              <li class="grid gap-0.5 text-[0.8125rem]">
                {#if entry.url == null}
                  <span class="text-sm font-semibold text-pretty"
                    >{entry.title}</span
                  >
                {:else}
                  <a
                    class="text-sm font-semibold text-pretty underline decoration-border underline-offset-4 hover:decoration-foreground"
                    href={entry.url}
                    rel="external noopener"
                    target="_blank"
                    >{entry.title}<ArrowUpRightIcon
                      class="ms-0.5 inline-block align-[-0.1em] text-muted-foreground block-3.5 inline-3.5"
                      aria-hidden="true"
                    /></a
                  >
                {/if}
                <span>{entry.publisher}</span>
                <span class="text-muted-foreground"
                  ><span class="font-semibold text-foreground"
                    >{STAGES[entry.stage]?.() ?? entry.stage}</span
                  >{#if entry.retrieved != null}{" · "}{m.provenance_retrieved({
                      date: date(entry.retrieved),
                    })}{/if}{#if entry.sets > 1}{" · "}{m.provenance_sets({
                      count: count(entry.sets),
                    })}{/if}</span
                >
                <span
                  class="text-muted-foreground"
                  title={entry.pipeline == null
                    ? undefined
                    : m.provenance_pipeline({ pipeline: entry.pipeline })}
                  >{m.provenance_changeset({
                    id: entry.changeset.slice(0, 8),
                    date: date(entry.published),
                  })}{#if entry.changesets > 1}{" · "}{m.provenance_changesets({
                      count: count(entry.changesets),
                    })}{/if}</span
                >
              </li>
            {/each}
          </ul>
          {const scan = $derived(trace.data.pages[0])}
          {#if scan != null}
            {const x0 = $derived(scan.box[0])}
            {const y0 = $derived(scan.box[1])}
            {const x1 = $derived(scan.box[2])}
            {const y1 = $derived(scan.box[3])}
            {const left = $derived(Math.max(0, x0 - 0.18) * scan.width)}
            {const top = $derived(Math.max(0, y0 - 0.05) * scan.height)}
            {const across = $derived(
              Math.min(1, x1 + 0.18) * scan.width - left,
            )}
            {const down = $derived(Math.min(1, y1 + 0.05) * scan.height - top)}
            <figure class="grid gap-1.5">
              <svg
                class="rounded-sm bg-white ring-1 ring-border inline-full"
                viewBox="{left} {top} {across} {down}"
                role="img"
                aria-label={m.provenance_crop_alt({ page: scan.page })}
              >
                <image
                  href="/pages/{scan.image}"
                  width={scan.width}
                  height={scan.height}
                />
                <rect
                  x={x0 * scan.width - 3}
                  y={y0 * scan.height - 2}
                  width={(x1 - x0) * scan.width + 6}
                  height={(y1 - y0) * scan.height + 4}
                  rx="2"
                  class="fill-primary/35 stroke-ring"
                  stroke-width="1.5"
                />
              </svg>
              <figcaption
                class="grid gap-1 text-[0.8125rem] text-pretty text-muted-foreground"
              >
                <span
                  >{m.provenance_scan({
                    page: scan.page,
                    title: scan.title,
                  })}</span
                >
                {#if !scan.isExact}
                  <span>{m.provenance_parts({ count: trace.data.count })}</span>
                {/if}
                <a
                  class="font-semibold text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
                  href="/pages/{scan.image}"
                  rel="external">{m.provenance_open_page()}</a
                >
              </figcaption>
            </figure>
          {/if}
        {/if}
      </section>
    {/if}

    {#if value == null}
      {#if sources.length > 0}
        <section class="grid gap-1.5">
          <h3 class="text-xs font-semibold text-muted-foreground">
            {m.provenance_sources()}
          </h3>
          <ul class="grid gap-1.5 text-sm/snug">
            {#each sources as source (source)}
              <li class="wrap-break-word">
                {#each source.split(", ") as part, index (index)}
                  {#if index > 0},
                  {/if}
                  {#if /^https?:\/\//.test(part)}
                    <a
                      class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                      href={part}
                      rel="external noopener">{new URL(part).host}</a
                    >
                  {:else}
                    {part}
                  {/if}
                {/each}
              </li>
            {/each}
          </ul>
        </section>
      {/if}
      <section class="grid gap-1.5">
        <h3 class="text-xs font-semibold text-muted-foreground">
          {m.provenance_methods()}
        </h3>
        <ul
          class="grid list-disc gap-1.5 ps-4 text-[0.8125rem] text-pretty text-muted-foreground"
        >
          <li>{m.method_share()}</li>
          <li>{m.term_valid()}</li>
          <li>{m.method_stage()}</li>
        </ul>
      </section>
    {/if}
  </div>
{/snippet}

{#if wide.current}
  <Popover.Root bind:open={provenance.isOpen}>
    <Popover.Content
      customAnchor={provenance.anchor}
      side="bottom"
      align="start"
      sideOffset={6}
      collisionPadding={12}
      aria-label={value == null
        ? m.provenance_title()
        : m.provenance_value_title()}
      class="block overflow-y-auto p-0 inline-[min(24rem,calc(100vw-1.5rem))] max-block-[min(36rem,var(--bits-popover-content-available-height))]"
      onCloseAutoFocus={restore}
    >
      {@render body()}
    </Popover.Content>
  </Popover.Root>
{:else}
  <Drawer.Root
    bind:open={provenance.isOpen}
    shouldScaleBackground={false}
    autoFocus
  >
    <Drawer.Content onCloseAutoFocus={restore}>
      <Drawer.Title class="sr-only">
        {value == null ? m.provenance_title() : m.provenance_value_title()}
      </Drawer.Title>
      <div class="pbe-[env(safe-area-inset-bottom)]">
        {@render body()}
      </div>
    </Drawer.Content>
  </Drawer.Root>
{/if}
