<script lang="ts">
  import type { ViewSpec } from "@ea/view-spec";
  import { replaceState } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { decodeId, encodeId } from "@ea/view-spec";
  import LinkIcon from "@lucide/svelte/icons/link";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import SearchIcon from "@lucide/svelte/icons/search";
  import XIcon from "@lucide/svelte/icons/x";
  import { createQuery, keepPreviousData } from "@tanstack/svelte-query";
  import { untrack } from "svelte";
  import { toast } from "svelte-sonner";
  import { SvelteSet } from "svelte/reactivity";
  import { Button, buttonVariants } from "#lib/components/ui/button/index.js";
  import * as Command from "#lib/components/ui/command/index.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Places from "./places.svelte";
  import Results from "./results.svelte";
  import Scatter from "./scatter.svelte";

  const { data }: { data: PageData } = $props();

  const KINDS = [
    { value: "bar", label: m.workbench_panel_results },
    { value: "table", label: m.workbench_panel_places },
    { value: "scatter", label: m.workbench_panel_scatter },
  ] as const;

  let spec = $state<ViewSpec | null>(untrack(() => data.spec));
  let isPicking = $state(false);
  let typed = $state("");
  let term = $state("");
  let focus = $state<string | null>(null);
  let hovered = $state<string | null>(null);
  const picked = new SvelteSet<string>();

  $effect(() => {
    const next = typed.trim();
    const timer = setTimeout(() => (term = next), 140);
    return () => clearTimeout(timer);
  });

  $effect(() => {
    const url = new URL(page.url.href);
    if (spec == null) {
      url.searchParams.delete("spec");
    } else {
      const bytes = new TextEncoder().encode(JSON.stringify(spec));
      let binary = "";
      for (const byte of bytes) {
        binary += String.fromCodePoint(byte);
      }
      url.searchParams.set(
        "spec",
        btoa(binary)
          .replaceAll("+", "-")
          .replaceAll("/", "_")
          .replaceAll("=", ""),
      );
    }
    if (url.search !== page.url.search) {
      void replaceState(
        url.search === ""
          ? resolve("/workbench")
          : resolve(`/workbench?${url.searchParams.toString()}`),
        {},
      );
    }
  });

  const found = createQuery(() => ({
    ...orpc.search.query.queryOptions({ input: { q: term, limit: 20 } }),
    enabled: term.length > 1,
    placeholderData: keepPreviousData,
  }));
  const hits = $derived(
    (found.data ?? []).filter((hit) => hit.type === "contest"),
  );

  const contest = createQuery(() => ({
    ...orpc.contests.get.queryOptions({
      input: {
        id: spec == null ? "" : decodeId(spec.data.contests[0] ?? ""),
        by: spec?.data.level,
        vs:
          spec?.data.compare == null
            ? undefined
            : "previous" in spec.data.compare
              ? "previous"
              : decodeId(spec.data.compare.contests[0] ?? ""),
        match: "party",
        group: "option",
      },
    }),
    enabled: spec != null,
    placeholderData: keepPreviousData,
  }));
  const current = $derived(spec == null ? null : (contest.data ?? null));
  const legend = $derived(current?.places?.legend ?? []);
  const focused = $derived(
    legend.find((entry) => entry.key === focus) ?? legend[0] ?? null,
  );
</script>

<svelte:head>
  <title>{m.workbench_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="grid content-start gap-4 px-4 pbs-6 pbe-16">
  <header class="grid gap-1.5">
    <h1
      class="font-serif text-[clamp(1.75rem,3vw,2.5rem)] leading-[1.02] font-[640] tracking-[-0.01em]"
    >
      {m.workbench_title()}
    </h1>
    <p class="text-[0.9375rem] text-muted-foreground max-inline-[60ch]">
      {m.workbench_lede()}
    </p>
  </header>

  <div class="flex flex-wrap items-center gap-2">
    <Popover.Root bind:open={isPicking}>
      <Popover.Trigger
        class={cn(
          "flex field items-center gap-2 ps-2.5 pe-2 text-start block-8 inline-full max-inline-md min-inline-0",
        )}
      >
        <SearchIcon class="shrink-0 text-muted-foreground block-4 inline-4" />
        <span class="sr-only">{m.workbench_contest()}:</span>
        <span
          class={cn("flex-1 truncate min-inline-0", {
            "text-muted-foreground": current == null,
          })}>{current?.name ?? m.workbench_contest_placeholder()}</span
        >
      </Popover.Trigger>
      <Popover.Content
        align="start"
        class="p-0 inline-[min(28rem,calc(100vw-2rem))]"
      >
        <Command.Root shouldFilter={false}>
          <Command.Input
            bind:value={typed}
            placeholder={m.workbench_contest_placeholder()}
          />
          <Command.List>
            {#if term.length > 1 && !found.isFetching}
              <Command.Empty>{m.workbench_contest_empty()}</Command.Empty>
            {/if}
            {#each hits as hit (hit.id)}
              <Command.Item
                value={hit.id}
                onSelect={() => {
                  spec = {
                    version: 1,
                    data: {
                      contests: [encodeId("ct", hit.id)],
                      measure: "votes",
                      channel: "all",
                      compare: spec?.data.compare ?? { previous: true },
                    },
                    filters: [],
                    panels: spec?.panels ?? [
                      { kind: "chart", chart: "bar" },
                      { kind: "table" },
                      { kind: "chart", chart: "scatter" },
                    ],
                  };
                  focus = null;
                  picked.clear();
                  isPicking = false;
                }}
                class="grid gap-0.5"
              >
                <span class="truncate">{hit.name}</span>
                <span class="truncate text-xs text-muted-foreground"
                  >{hit.context}{#if hit.date != null}, {date(
                      hit.date,
                    )}{/if}</span
                >
              </Command.Item>
            {/each}
          </Command.List>
        </Command.Root>
      </Popover.Content>
    </Popover.Root>
    {#if spec != null}
      {const data = $derived(spec.data)}
      <button
        type="button"
        class={cn(
          "flex keycap items-center rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold block-8 hover:bg-secondary",
          {
            "bg-primary text-primary-foreground hover:bg-primary":
              data.compare != null,
          },
        )}
        aria-pressed={data.compare != null}
        onclick={() => {
          data.compare = data.compare == null ? { previous: true } : undefined;
        }}>{m.workbench_compare()}</button
      >
      {#if (current?.places?.levels.length ?? 0) > 1}
        <div class="flex" role="group" aria-label={m.workbench_level()}>
          {#each current?.places?.levels ?? [] as level (level.code)}
            <button
              type="button"
              class={cn(
                "-ms-px flex keycap items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold block-8 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                {
                  "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                    level.code === current?.places?.level,
                },
              )}
              aria-pressed={level.code === current?.places?.level}
              onclick={() => {
                data.level = level.code;
                picked.clear();
              }}>{level.noun}</button
            >
          {/each}
        </div>
      {/if}
      <div class="ms-auto flex items-center gap-2">
        {#if picked.size > 0}
          <Button variant="ghost" size="sm" onclick={() => picked.clear()}>
            <XIcon />{m.workbench_selected({ count: picked.size })}
            <span class="sr-only">{m.workbench_clear()}</span>
          </Button>
        {/if}
        {#if current?.path != null}
          <a
            class={buttonVariants({ variant: "ghost", size: "sm" })}
            href={resolve("/[...path]", { path: current.path })}
            >{m.workbench_open()}</a
          >
        {/if}
        <Button
          variant="outline"
          size="sm"
          onclick={async () => {
            await navigator.clipboard.writeText(location.href);
            toast.success(m.workbench_copied());
          }}><LinkIcon />{m.workbench_copy()}</Button
        >
        <DropdownMenu.Root>
          <DropdownMenu.Trigger
            class={buttonVariants({ variant: "default", size: "sm" })}
          >
            <PlusIcon />{m.workbench_add()}
          </DropdownMenu.Trigger>
          <DropdownMenu.Content align="end">
            {#each KINDS as kind (kind.value)}
              <DropdownMenu.Item
                onSelect={() => {
                  spec?.panels.push(
                    kind.value === "table"
                      ? { kind: "table" }
                      : { kind: "chart", chart: kind.value },
                  );
                }}>{kind.label()}</DropdownMenu.Item
              >
            {/each}
          </DropdownMenu.Content>
        </DropdownMenu.Root>
      </div>
    {/if}
  </div>

  {#if spec == null}
    <p
      class="grid place-items-center border border-dashed border-border text-muted-foreground min-block-64"
    >
      {m.workbench_start()}
    </p>
  {:else if current != null}
    <div
      class={cn("grid gap-4 lg:grid-cols-2", {
        "opacity-60 transition-opacity": contest.isFetching,
      })}
    >
      {#each spec.panels as panel, index (index)}
        {const kind = $derived(
          panel.kind === "table"
            ? "table"
            : panel.kind === "chart"
              ? panel.chart
              : panel.kind,
        )}
        <section
          class={cn("grid content-start border bg-card min-inline-0", {
            "lg:col-span-2": kind === "table",
          })}
          aria-labelledby="panel-{index}"
        >
          <header
            class="flex items-center justify-between gap-2 border-be px-3.5 block-10"
          >
            <h2 id="panel-{index}" class="text-[0.8125rem] font-bold">
              {KINDS.find((entry) => entry.value === kind)?.label() ?? kind}
            </h2>
            <Button
              variant="ghost"
              size="icon-sm"
              onclick={() => spec?.panels.splice(index, 1)}
            >
              <XIcon />
              <span class="sr-only">{m.workbench_remove()}</span>
            </Button>
          </header>
          {#if kind === "bar"}
            <Results
              contest={current}
              focus={focused?.key ?? null}
              {picked}
              onfocus={(key) => (focus = key)}
            />
          {:else if kind === "table"}
            <Places contest={current} focus={focused} {picked} bind:hovered />
          {:else if kind === "scatter"}
            <Scatter contest={current} focus={focused} {picked} bind:hovered />
          {/if}
        </section>
      {/each}
    </div>
  {/if}
</main>
