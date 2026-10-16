<script lang="ts">
  import XIcon from "@lucide/svelte/icons/x";
  import { createQuery } from "@tanstack/svelte-query";
  import { MediaQuery } from "svelte/reactivity";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import { provenance } from "#lib/provenance.svelte.js";
  import { orpc } from "#lib/rpc.js";

  const wide = new MediaQuery("min-width: 640px");
  let sources = $state<Array<string>>([]);

  const value = $derived(provenance.value);
  const trace = createQuery(() => ({
    ...orpc.contests.trace.queryOptions({
      input: {
        path: value?.path ?? "",
        key: value?.key ?? null,
        area: value?.area ?? null,
      },
    }),
    enabled: provenance.isOpen && value?.path != null,
    staleTime: Infinity,
    meta: { quiet: true },
  }));
  const methods = $derived(
    [
      value?.measure === "share" ? m.method_share() : null,
      value?.measure === "votes" || value?.measure === "share"
        ? m.term_valid()
        : null,
      value?.measure === "margin" ? m.term_margin() : null,
      value?.measure === "swing" ? m.method_swing() : null,
      value == null || value.measure === "seats" ? m.method_seats() : null,
      m.method_stage(),
    ].filter((line) => line != null),
  );

  $effect(() => {
    if (provenance.isOpen) {
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
</script>

<Drawer.Root
  bind:open={provenance.isOpen}
  direction={wide.current ? "right" : "bottom"}
  shouldScaleBackground={false}
>
  <Drawer.Content class="sm:max-inline-md">
    <div
      class="grid gap-5 px-5 pbs-5 pbe-[max(1.25rem,env(safe-area-inset-bottom))]"
    >
      <div class="flex items-start justify-between gap-3">
        <div class="grid gap-1">
          <Drawer.Title class="heading">
            {value == null ? m.provenance_title() : m.provenance_value_title()}
          </Drawer.Title>
          {#if value != null}
            <Drawer.Description class="flex items-baseline gap-2 text-sm">
              <span class="text-muted-foreground">{value.label}</span>
              <span class="font-figures font-semibold tabular-nums"
                >{value.figure}</span
              >
            </Drawer.Description>
          {/if}
        </div>
        <Drawer.Close
          class="-me-2 grid shrink-0 place-items-center rounded-sm text-muted-foreground block-9 inline-9 hover:bg-secondary"
          aria-label={m.map_sheet_close()}
        >
          <XIcon class="block-4 inline-4" />
        </Drawer.Close>
      </div>

      {#if value?.path != null}
        {#if trace.isPending}
          <p class="text-sm text-muted-foreground">{m.provenance_loading()}</p>
        {:else if trace.data != null && trace.data.pages.length > 0}
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
            <figure class="grid gap-2">
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
              <figcaption class="grid gap-1 text-[0.8125rem] text-pretty">
                <span
                  >{m.provenance_scan({
                    page: scan.page,
                    title: scan.title,
                  })}</span
                >
                {#if !scan.isExact}
                  <span class="text-muted-foreground"
                    >{m.provenance_parts({ count: trace.data.count })}</span
                  >
                {/if}
                <span class="flex flex-wrap gap-x-4 gap-y-1">
                  <a
                    class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
                    href="/pages/{scan.image}"
                    rel="external">{m.provenance_open_page()}</a
                  >
                  {#if scan.url != null}
                    <a
                      class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
                      href={scan.url}
                      rel="external noopener">{scan.publisher}</a
                    >
                  {/if}
                </span>
              </figcaption>
            </figure>
          {/if}
        {:else}
          <p class="text-sm text-muted-foreground">{m.provenance_file()}</p>
        {/if}
      {/if}

      {#if sources.length > 0}
        <section class="grid gap-1.5">
          <h3 class="text-[0.8125rem] font-bold">{m.provenance_sources()}</h3>
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
        <h3 class="text-[0.8125rem] font-bold">{m.provenance_methods()}</h3>
        <ul
          class="grid list-disc gap-1.5 ps-4 text-sm/snug text-pretty text-muted-foreground"
        >
          {#each methods as line (line)}
            <li>{line}</li>
          {/each}
        </ul>
      </section>
    </div>
  </Drawer.Content>
</Drawer.Root>
