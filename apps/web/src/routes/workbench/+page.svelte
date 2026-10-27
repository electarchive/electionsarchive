<script lang="ts">
  import type { ViewSpec } from "@ea/view-spec";
  import PlaceMap from "#routes/[...path]/map.svelte";
  import Seats from "#routes/[...path]/seats.svelte";
  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { decodeId, encodeId } from "@ea/view-spec";
  import BookmarkIcon from "@lucide/svelte/icons/bookmark";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import GripIcon from "@lucide/svelte/icons/grip";
  import LinkIcon from "@lucide/svelte/icons/link";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import SearchIcon from "@lucide/svelte/icons/search";
  import XIcon from "@lucide/svelte/icons/x";
  import { createQuery, keepPreviousData } from "@tanstack/svelte-query";
  import { untrack } from "svelte";
  import { toast } from "svelte-sonner";
  import { on } from "svelte/events";
  import { MediaQuery, SvelteSet } from "svelte/reactivity";
  import { Button, buttonVariants } from "#lib/components/ui/button/index.js";
  import * as Command from "#lib/components/ui/command/index.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { getConfirmationContext } from "#lib/stores/confirmation.svelte.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Places from "./places.svelte";
  import Results from "./results.svelte";
  import Scatter from "./scatter.svelte";

  const { data }: { data: PageData } = $props();

  const KINDS = [
    { value: "map", label: m.workbench_panel_map },
    { value: "bar", label: m.workbench_panel_results },
    { value: "hemicycle", label: m.workbench_panel_coalition },
    { value: "table", label: m.workbench_panel_places },
    { value: "scatter", label: m.workbench_panel_scatter },
  ] as const;
  const COLUMNS = 12;
  const ROW = 32;
  const GAP = 16;

  const confirmation = getConfirmationContext();

  let spec = $state<ViewSpec | null>(untrack(() => data.spec));
  let isPicking = $state(false);
  let isSaving = $state(false);
  let visibility = $state<"private" | "unlisted">(
    untrack(() =>
      data.view?.visibility === "unlisted" ? "unlisted" : "private",
    ),
  );
  let typed = $state("");
  let term = $state("");
  let focus = $state<string | null>(null);
  let hovered = $state<string | null>(null);
  let active = $state(0);
  let grid = $state<HTMLElement | null>(null);
  const picked = new SvelteSet<string>();
  const wide = new MediaQuery("min-width: 64rem");

  $effect(() => {
    const next = data.spec;
    const shared = data.view;
    untrack(() => {
      spec = next;
      visibility = shared?.visibility === "unlisted" ? "unlisted" : "private";
      picked.clear();
      focus = null;
    });
  });

  $effect(() => {
    if (data.isMissing) {
      toast.error(m.workbench_missing());
    }
  });

  $effect(() => {
    const next = typed.trim();
    const timer = setTimeout(() => (term = next), 140);
    return () => clearTimeout(timer);
  });

  const isDirty = $derived(
    spec != null &&
      (data.view == null ||
        JSON.stringify(spec) !== JSON.stringify(data.view.spec)),
  );

  $effect(() => {
    const url = new URL(page.url.href);
    if (spec == null || !isDirty) {
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
      void goto(
        url.search === ""
          ? resolve("/workbench")
          : resolve(`/workbench?${url.searchParams.toString()}`),
        { state: {}, shallow: true, replace: true },
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

  const outlines = createQuery(() => ({
    ...orpc.contests.boundaries.queryOptions({
      input: {
        id: spec == null ? "" : decodeId(spec.data.contests[0] ?? ""),
        level: current?.places?.level,
      },
    }),
    enabled: current?.places != null,
  }));
  const drawn = $derived.by(() => {
    const places = current?.places;
    const found = outlines.data ?? [];
    const level = found.find((entry) => entry.scheme === places?.level);
    const own = found.find(
      (entry) => entry.k != null && entry.scheme !== places?.level,
    );
    return places == null || level?.tiles == null || own?.frame == null
      ? null
      : {
          places,
          tiles: level.tiles,
          own:
            own.tiles == null || own.k == null
              ? null
              : { tiles: own.tiles, k: own.k },
          frame: own.frame,
          isApproximate: level.isApproximate,
          noun:
            places.levels.find((entry) => entry.code === places.level)?.noun ??
            level.noun,
        };
  });
  const span = $derived.by(() => {
    const target = current?.target;
    const own = current?.election;
    if (
      target?.date == null ||
      target.year == null ||
      own?.date == null ||
      own.year == null
    ) {
      return null;
    }
    const forward = target.date > own.date;
    return {
      from: String(forward ? own.year : target.year),
      to: String(forward ? target.year : own.year),
      forward,
    };
  });
  const change = $derived.by(() => {
    if (span == null || current?.chamber == null) {
      return null;
    }
    const moves = current.rows.flatMap((row) => {
      const before = span.forward ? row.current : row.earlier;
      const after = span.forward ? row.earlier : row.current;
      return before?.seats == null
        ? []
        : [[row.key, (after?.seats ?? 0) - before.seats] as const];
    });
    return moves.length === 0 ? null : Object.fromEntries(moves);
  });

  const panels = $derived.by(() => {
    const placed: Array<{ x: number; y: number; w: number; h: number }> = [];
    return (spec?.panels ?? []).map((panel) => {
      const kind =
        panel.kind === "table"
          ? "table"
          : panel.kind === "chart"
            ? panel.chart
            : panel.kind;
      let area = panel.area;
      if (area == null) {
        const w = kind === "table" || kind === "map" ? COLUMNS : 6;
        const h = kind === "map" ? 18 : kind === "table" ? 14 : 12;
        let y = 0;
        let x = 0;
        for (; ; y += 1) {
          const free = [0, 6].find(
            (start) =>
              start + w <= COLUMNS &&
              placed.every(
                (other) =>
                  start + w <= other.x ||
                  other.x + other.w <= start ||
                  y + h <= other.y ||
                  other.y + other.h <= y,
              ),
          );
          if (free != null) {
            x = free;
            break;
          }
        }
        area = { x, y, w, h };
      }
      placed.push(area);
      return {
        kind,
        label: KINDS.find((entry) => entry.value === kind)?.label() ?? kind,
        area,
      };
    });
  });

  const place = (
    index: number,
    next: { x: number; y: number; w: number; h: number },
  ) => {
    if (spec == null) {
      return;
    }
    const areas = panels.map((panel) => ({ ...panel.area }));
    const w = Math.min(Math.max(next.w, 3), COLUMNS);
    areas[index] = {
      w,
      h: Math.max(next.h, 4),
      x: Math.min(Math.max(next.x, 0), COLUMNS - w),
      y: Math.max(next.y, 0),
    };
    const settled = [index];
    const order = areas
      .map((_, at) => at)
      .filter((at) => at !== index)
      .toSorted((a, b) => (areas[a]?.y ?? 0) - (areas[b]?.y ?? 0));
    for (const at of order) {
      const area = areas[at];
      if (area == null) {
        continue;
      }
      let isMoved = true;
      while (isMoved) {
        isMoved = false;
        for (const other of settled) {
          const fixed = areas[other];
          if (
            fixed != null &&
            area.x < fixed.x + fixed.w &&
            fixed.x < area.x + area.w &&
            area.y < fixed.y + fixed.h &&
            fixed.y < area.y + area.h
          ) {
            area.y = fixed.y + fixed.h;
            isMoved = true;
          }
        }
      }
      settled.push(at);
    }
    for (const [at, panel] of spec.panels.entries()) {
      panel.area = areas[at];
    }
  };

  const drag = (
    event: PointerEvent,
    index: number,
    mode: "move" | "resize",
  ) => {
    const start = panels[index]?.area;
    if (grid == null || start == null || event.button !== 0) {
      return;
    }
    const target = event.currentTarget;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    event.preventDefault();
    target.setPointerCapture(event.pointerId);
    const column = (grid.clientWidth + GAP) / COLUMNS;
    const row = ROW + GAP;
    const origin = { x: event.clientX, y: event.clientY };
    const onmove = (moved: PointerEvent) => {
      const dx = Math.round((moved.clientX - origin.x) / column);
      const dy = Math.round((moved.clientY - origin.y) / row);
      const area = panels[index]?.area;
      const next =
        mode === "move"
          ? { ...start, x: start.x + dx, y: start.y + dy }
          : { ...start, w: start.w + dx, h: start.h + dy };
      if (
        area?.x !== next.x ||
        area.y !== next.y ||
        area.w !== next.w ||
        area.h !== next.h
      ) {
        place(index, next);
      }
    };
    const stops = [
      on(target, "pointermove", onmove),
      on(target, "pointerup", () => {
        for (const stop of stops) {
          stop();
        }
      }),
      on(target, "pointercancel", () => {
        for (const stop of stops) {
          stop();
        }
      }),
    ];
  };

  const download = (name: string, text: string, type: string) => {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    URL.revokeObjectURL(url);
  };
</script>

<svelte:head>
  <title>{spec?.title ?? m.workbench_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="grid content-start gap-4 px-4 pbs-6 pbe-16">
  <header class="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
    <div class="grid gap-1.5">
      <h1 class="title">
        {spec?.title ?? m.workbench_title()}
      </h1>
      <p class="text-[0.9375rem] text-muted-foreground max-inline-[60ch]">
        {#if data.view != null && !data.view.isOwner}
          {m.workbench_shared()}
        {:else}
          {m.workbench_lede()}
        {/if}
      </p>
    </div>
    <div class="flex flex-wrap items-center gap-2">
      {#if spec != null && data.view?.isOwner === true}
        <span
          class={cn("text-[0.8125rem] text-muted-foreground", {
            "highlighter px-1 font-semibold text-foreground": isDirty,
          })}
          aria-live="polite"
          >{isDirty
            ? m.workbench_status_dirty()
            : m.workbench_status_saved()}</span
        >
      {/if}
      <DropdownMenu.Root>
        <DropdownMenu.Trigger
          class={buttonVariants({ variant: "outline", size: "sm" })}
        >
          <BookmarkIcon />{m.workbench_views()}
        </DropdownMenu.Trigger>
        <DropdownMenu.Content align="end" class="max-inline-80">
          {#if data.views.length === 0}
            <p class="px-2 py-1.5 text-sm text-muted-foreground">
              {m.workbench_views_empty()}
            </p>
          {/if}
          {#each data.views as view (view.id)}
            <DropdownMenu.Item
              class={cn("grid gap-0.5", {
                "bg-primary/15": view.id === data.view?.id,
              })}
              onSelect={async () => goto(resolve(`/workbench?view=${view.id}`))}
            >
              <span class="truncate font-semibold"
                >{view.title ?? m.workbench_view_untitled()}</span
              >
              <span class="text-xs text-muted-foreground"
                >{m.workbench_view_updated({
                  date: date(view.updatedAt.slice(0, 10)),
                })}{#if view.visibility === "unlisted"}, {m
                    .workbench_unlisted()
                    .toLocaleLowerCase()}{/if}</span
              >
            </DropdownMenu.Item>
          {/each}
          <DropdownMenu.Separator />
          <DropdownMenu.Item onSelect={async () => goto(resolve("/workbench"))}>
            <PlusIcon />{m.workbench_new()}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Root>
      {#if spec != null}
        <Popover.Root bind:open={isSaving}>
          <Popover.Trigger
            class={buttonVariants({
              variant:
                isDirty || data.view?.isOwner !== true ? "default" : "outline",
              size: "sm",
            })}
          >
            {data.view == null
              ? m.workbench_save()
              : data.view.isOwner
                ? m.workbench_save_changes()
                : m.workbench_save_copy()}
          </Popover.Trigger>
          <Popover.Content
            align="end"
            class="inline-[min(22rem,calc(100vw-2rem))]"
          >
            <form
              method="POST"
              action="?/save"
              class="grid gap-3"
              use:enhance={() =>
                async ({ result }) => {
                  if (
                    result.type === "success" &&
                    typeof result.data?.id === "string"
                  ) {
                    isSaving = false;
                    toast.success(m.workbench_saved());
                    await goto(resolve(`/workbench?view=${result.data.id}`), {
                      invalidateAll: true,
                    });
                  } else {
                    toast.error(m.workbench_save_failed());
                  }
                }}
            >
              <input type="hidden" name="spec" value={JSON.stringify(spec)} />
              {#if data.view?.isOwner === true}
                <input type="hidden" name="id" value={data.view.id} />
              {/if}
              <label class="grid gap-1.5 text-[0.8125rem] font-semibold">
                {m.workbench_title_label()}
                <Input
                  value={spec.title ?? ""}
                  placeholder={m.workbench_title_placeholder()}
                  maxlength={200}
                  oninput={(event) => {
                    if (spec != null) {
                      const value = event.currentTarget.value.trim();
                      spec.title = value === "" ? undefined : value;
                    }
                  }}
                />
              </label>
              <fieldset class="grid gap-1.5">
                <legend class="pbe-1.5 text-[0.8125rem] font-semibold"
                  >{m.workbench_visibility()}</legend
                >
                <input type="hidden" name="visibility" value={visibility} />
                <div class="flex" role="group">
                  {#each [{ value: "private", label: m.workbench_private }, { value: "unlisted", label: m.workbench_unlisted }] as const as option (option.value)}
                    <button
                      type="button"
                      class={cn(
                        "-ms-px flex flex-1 items-center justify-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-8 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                        {
                          "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                            visibility === option.value,
                        },
                      )}
                      aria-pressed={visibility === option.value}
                      onclick={() => (visibility = option.value)}
                      >{option.label()}</button
                    >
                  {/each}
                </div>
              </fieldset>
              <div class="flex items-center justify-between gap-2">
                {#if data.view?.isOwner === true}
                  <Button
                    variant="ghost"
                    size="sm"
                    class="text-destructive"
                    onclick={async () => {
                      const view = data.view;
                      if (
                        view == null ||
                        !(await confirmation.confirm({
                          title: m.workbench_delete_title(),
                          description: m.workbench_delete_description(),
                          confirmText: m.workbench_delete(),
                          cancelText: m.workbench_cancel(),
                          variant: "destructive",
                        }))
                      ) {
                        return;
                      }
                      const body = new FormData();
                      body.set("id", view.id);
                      const response = await fetch("?/delete", {
                        method: "POST",
                        body,
                        headers: { "x-sveltekit-action": "true" },
                      });
                      if (response.ok) {
                        isSaving = false;
                        toast.success(m.workbench_deleted());
                        await goto(resolve("/workbench"), {
                          invalidateAll: true,
                        });
                      }
                    }}>{m.workbench_delete()}</Button
                  >
                {/if}
                <Button type="submit" size="sm" class="ms-auto"
                  >{data.view?.isOwner === true
                    ? m.workbench_save_changes()
                    : data.view == null
                      ? m.workbench_save()
                      : m.workbench_save_copy()}</Button
                >
              </div>
            </form>
          </Popover.Content>
        </Popover.Root>
      {/if}
    </div>
  </header>

  <div class="flex flex-wrap items-center gap-2">
    <Popover.Root bind:open={isPicking}>
      <Popover.Trigger
        class="flex field items-center gap-2 ps-2.5 pe-2 text-start block-8 inline-full max-inline-md min-inline-0"
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
                    title: spec?.title,
                    data: {
                      contests: [encodeId("ct", hit.id)],
                      measure: "votes",
                      channel: "all",
                      compare: spec?.data.compare ?? { previous: true },
                    },
                    filters: [],
                    panels: spec?.panels ?? [
                      { kind: "map", color: "winner" },
                      { kind: "chart", chart: "bar" },
                      { kind: "chart", chart: "scatter" },
                      { kind: "table" },
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
      {const selector = $derived(spec.data)}
      <button
        type="button"
        class={cn(
          "flex items-center rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-8 hover:bg-secondary",
          {
            "bg-primary text-primary-foreground hover:bg-primary":
              selector.compare != null,
          },
        )}
        aria-pressed={selector.compare != null}
        onclick={() => {
          selector.compare =
            selector.compare == null ? { previous: true } : undefined;
        }}>{m.workbench_compare()}</button
      >
      {#if (current?.places?.levels.length ?? 0) > 1}
        <div class="flex" role="group" aria-label={m.workbench_level()}>
          {#each current?.places?.levels ?? [] as level (level.code)}
            <button
              type="button"
              class={cn(
                "-ms-px flex items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-8 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                {
                  "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                    level.code === current?.places?.level,
                },
              )}
              aria-pressed={level.code === current?.places?.level}
              onclick={() => {
                selector.level = level.code;
                picked.clear();
              }}>{level.noun}</button
            >
          {/each}
        </div>
      {/if}
      <div class="ms-auto flex flex-wrap items-center gap-2">
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
            class={buttonVariants({ variant: "outline", size: "sm" })}
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
                      : kind.value === "map"
                        ? { kind: "map", color: "winner" }
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
    <section
      class="grid content-start gap-3 rounded-sm bg-card p-4 ring-1 ring-border"
      aria-labelledby="examples-title"
    >
      <div class="grid gap-1">
        <h2 id="examples-title" class="heading">
          {m.workbench_examples()}
        </h2>
        <p class="text-sm text-muted-foreground max-inline-[60ch]">
          {m.workbench_start()}
        </p>
      </div>
      <ul
        class="grid gap-px overflow-hidden rounded-sm bg-border ring-1 ring-border sm:grid-cols-2 lg:grid-cols-3"
      >
        {#each data.examples as example (example.id)}
          <li class="grid">
            <button
              type="button"
              class="grid content-start gap-1 bg-card p-3 text-start hover:bg-secondary/60 focus-visible:relative"
              onclick={() => {
                spec = {
                  version: 1,
                  data: {
                    contests: [encodeId("ct", example.id)],
                    measure: "votes",
                    channel: "all",
                    compare: { previous: true },
                  },
                  filters: [],
                  panels: [
                    { kind: "map", color: "winner" },
                    { kind: "chart", chart: "bar" },
                    { kind: "chart", chart: "scatter" },
                    { kind: "table" },
                  ],
                };
              }}
            >
              <span class="text-lg/tight font-[720] text-balance"
                >{example.name}</span
              >
              <span class="text-[0.8125rem] text-muted-foreground"
                >{example.election}{#if example.date != null}, {date(
                    example.date,
                  )}{/if}</span
              >
            </button>
          </li>
        {/each}
      </ul>
    </section>
  {:else if current != null}
    {#if !wide.current}
      <nav
        class="flex gap-1.5 overflow-x-auto overflow-y-hidden overscroll-x-contain"
        aria-label={m.workbench_panels()}
      >
        {#each panels as panel, index (index)}
          <button
            type="button"
            class={cn(
              "shrink-0 rounded-full border border-input px-3 text-[0.8125rem] font-semibold block-8",
              { "bg-primary text-primary-foreground": active === index },
            )}
            aria-current={active === index ? "true" : undefined}
            onclick={() => {
              grid?.children[index]?.scrollIntoView({
                behavior: "smooth",
                block: "nearest",
                inline: "start",
              });
            }}>{panel.label}</button
          >
        {/each}
      </nav>
    {/if}
    <div
      bind:this={grid}
      class={cn(
        {
          "grid grid-cols-12 gap-4": wide.current,
          "-mx-4 flex snap-x snap-mandatory scroll-px-4 gap-4 overflow-x-auto overflow-y-hidden overscroll-x-contain px-4":
            !wide.current,
        },
        { "opacity-60 transition-opacity": contest.isFetching },
      )}
      style:grid-auto-rows={wide.current ? `${ROW}px` : undefined}
      onscroll={(event) => {
        const element = event.currentTarget;
        active = Math.round(element.scrollLeft / (element.clientWidth - 16));
      }}
    >
      {#each panels as panel, index (index)}
        <section
          class={cn(
            "relative grid grid-rows-[auto_minmax(0,1fr)] border bg-card min-inline-0",
            {
              "col-span-(--w) col-start-(--x) row-span-(--h) row-start-(--y)":
                wide.current,
              "shrink-0 snap-start content-start self-start inline-full":
                !wide.current,
            },
          )}
          style:--x={panel.area.x + 1}
          style:--y={panel.area.y + 1}
          style:--w={panel.area.w}
          style:--h={panel.area.h}
          aria-labelledby="panel-{index}"
        >
          <header
            class={cn(
              "flex items-center gap-1 border-be ps-1.5 pe-1 block-10",
              {
                "cursor-grab touch-none active:cursor-grabbing": wide.current,
                "ps-3.5": !wide.current,
              },
            )}
            {@attach (header: HTMLElement) => {
              const onpointerdown = (event: PointerEvent) => {
                if (
                  wide.current &&
                  event.target instanceof Element &&
                  event.target.closest("button, a, [role=menuitem]") == null
                ) {
                  drag(event, index, "move");
                }
              };
              return on(header, "pointerdown", onpointerdown);
            }}
          >
            {#if wide.current}
              <button
                type="button"
                class="grid place-items-center rounded-xs text-muted-foreground block-7 inline-6 hover:text-foreground"
                title={m.workbench_arrange_hint()}
                aria-label={m.workbench_arrange({ name: panel.label })}
                onpointerdown={(event) => drag(event, index, "move")}
                onkeydown={(event) => {
                  const step = {
                    ArrowLeft: [-1, 0],
                    ArrowRight: [1, 0],
                    ArrowUp: [0, -1],
                    ArrowDown: [0, 1],
                  }[event.key];
                  if (step == null) {
                    return;
                  }
                  event.preventDefault();
                  const [dx = 0, dy = 0] = step;
                  place(
                    index,
                    event.shiftKey
                      ? {
                          ...panel.area,
                          w: panel.area.w + dx,
                          h: panel.area.h + dy,
                        }
                      : {
                          ...panel.area,
                          x: panel.area.x + dx,
                          y: panel.area.y + dy,
                        },
                  );
                }}
              >
                <GripIcon class="block-3.5 inline-3.5" />
              </button>
            {/if}
            <h2
              id="panel-{index}"
              class="flex-1 truncate text-[0.8125rem] font-bold"
            >
              {panel.label}
            </h2>
            <DropdownMenu.Root>
              <DropdownMenu.Trigger
                class={buttonVariants({ variant: "ghost", size: "icon-sm" })}
              >
                <DownloadIcon />
                <span class="sr-only">{m.workbench_export()}</span>
              </DropdownMenu.Trigger>
              <DropdownMenu.Content align="end">
                {#if panel.kind === "bar" || panel.kind === "scatter"}
                  <DropdownMenu.Item
                    onSelect={() => {
                      const at = legend.findIndex(
                        (entry) => entry.key === focused?.key,
                      );
                      const chart =
                        panel.kind === "bar"
                          ? {
                              data: {
                                values: current.rows.flatMap((row) =>
                                  row.current == null
                                    ? []
                                    : [
                                        {
                                          option: row.label,
                                          share: row.current.share,
                                          earlier: row.earlier?.share ?? null,
                                          color: row.color ?? "#8a8578",
                                        },
                                      ],
                                ),
                              },
                              encoding: {
                                y: {
                                  field: "option",
                                  type: "nominal",
                                  sort: "-x",
                                  title: null,
                                },
                              },
                              layer: [
                                {
                                  mark: "bar",
                                  encoding: {
                                    x: {
                                      field: "share",
                                      type: "quantitative",
                                      axis: { format: ".0%" },
                                      title: m.col_share(),
                                    },
                                    color: {
                                      field: "color",
                                      type: "nominal",
                                      scale: null,
                                    },
                                  },
                                },
                                {
                                  mark: { type: "tick", color: "black" },
                                  encoding: {
                                    x: {
                                      field: "earlier",
                                      type: "quantitative",
                                    },
                                  },
                                },
                              ],
                            }
                          : {
                              data: {
                                values: (current.places?.rows ?? []).flatMap(
                                  (row) => {
                                    const earlier = row.earlier?.[at];
                                    const share = row.shares[at];
                                    return earlier == null || share == null
                                      ? []
                                      : [
                                          {
                                            place: row.name,
                                            before: earlier,
                                            now: share,
                                            leader:
                                              row.leader == null
                                                ? null
                                                : (legend[row.leader]?.label ??
                                                  null),
                                          },
                                        ];
                                  },
                                ),
                              },
                              layer: [
                                {
                                  mark: { type: "point", filled: true },
                                  encoding: {
                                    x: {
                                      field: "before",
                                      type: "quantitative",
                                      axis: { format: ".0%" },
                                    },
                                    y: {
                                      field: "now",
                                      type: "quantitative",
                                      axis: { format: ".0%" },
                                    },
                                    color: {
                                      field: "leader",
                                      type: "nominal",
                                      scale: {
                                        domain: legend.map(
                                          (entry) => entry.label,
                                        ),
                                        range: legend.map(
                                          (entry) => entry.color ?? "#8a8578",
                                        ),
                                      },
                                    },
                                    tooltip: [
                                      { field: "place" },
                                      { field: "before", format: ".1%" },
                                      { field: "now", format: ".1%" },
                                    ],
                                  },
                                },
                              ],
                            };
                      download(
                        `${current.path?.replaceAll("/", "-") ?? "view"}-${panel.kind}.vl.json`,
                        JSON.stringify(
                          {
                            $schema:
                              "https://vega.github.io/schema/vega-lite/v5.json",
                            title: `${current.name}, ${current.scope.name}`,
                            width: 480,
                            ...chart,
                          },
                          null,
                          2,
                        ),
                        "application/json",
                      );
                    }}>{m.workbench_export_vega()}</DropdownMenu.Item
                  >
                {/if}
                {#if panel.kind === "table" || panel.kind === "bar"}
                  <DropdownMenu.Item
                    onSelect={() => {
                      const quote = (value: string | number | null) =>
                        value == null
                          ? ""
                          : typeof value === "number"
                            ? String(value)
                            : `"${value.replaceAll('"', '""')}"`;
                      const lines =
                        panel.kind === "bar"
                          ? [
                              [m.col_option(), m.col_votes(), m.col_share()],
                              ...current.rows.flatMap((row) =>
                                row.current == null
                                  ? []
                                  : [
                                      [
                                        row.label,
                                        row.current.votes,
                                        row.current.share,
                                      ],
                                    ],
                              ),
                            ]
                          : [
                              [
                                current.places?.level ?? "",
                                m.col_votes(),
                                ...legend.map((entry) => entry.label),
                              ],
                              ...(current.places?.rows ?? []).map((row) => [
                                row.name,
                                row.valid,
                                ...row.shares,
                              ]),
                            ];
                      download(
                        `${current.path?.replaceAll("/", "-") ?? "view"}-${panel.kind}.csv`,
                        lines
                          .map((line) => line.map(quote).join(","))
                          .join("\n"),
                        "text/csv",
                      );
                    }}>{m.workbench_export_csv()}</DropdownMenu.Item
                  >
                {/if}
                <DropdownMenu.Item
                  onSelect={() => {
                    download(
                      "view.json",
                      JSON.stringify(spec, null, 2),
                      "application/json",
                    );
                  }}>{m.workbench_export_spec()}</DropdownMenu.Item
                >
              </DropdownMenu.Content>
            </DropdownMenu.Root>
            <Button
              variant="ghost"
              size="icon-sm"
              onclick={() => spec?.panels.splice(index, 1)}
            >
              <XIcon />
              <span class="sr-only">{m.workbench_remove()}</span>
            </Button>
          </header>
          <div class={cn({ "overflow-auto": wide.current })}>
            {#if panel.kind === "bar"}
              <Results
                contest={current}
                focus={focused?.key ?? null}
                {picked}
                onfocus={(key) => (focus = key)}
              />
            {:else if panel.kind === "table"}
              <Places contest={current} focus={focused} {picked} bind:hovered />
            {:else if panel.kind === "map"}
              {#if drawn == null}
                <p class="px-3 py-6 text-sm text-muted-foreground">
                  {m.workbench_map_none()}
                </p>
              {:else}
                {#key drawn.tiles}
                  <PlaceMap
                    places={drawn.places}
                    colors={legend.map((item) => swatch(item.color, item.key))}
                    tiles={drawn.tiles}
                    own={drawn.own}
                    frame={drawn.frame}
                    isApproximate={drawn.isApproximate}
                    noun={drawn.noun}
                    votes={m.col_votes()}
                    names={legend.map((item) =>
                      item.person == null
                        ? (item.short ?? item.label)
                        : item.label,
                    )}
                    levels={[]}
                    {span}
                  />
                {/key}
              {/if}
            {:else if panel.kind === "hemicycle"}
              {#if current.chamber == null}
                <p class="px-3 py-6 text-sm text-muted-foreground">
                  {m.workbench_coalition_none()}
                </p>
              {:else}
                <Seats
                  parties={current.chamber.parties}
                  places={current.chamber.places}
                  here={current.chamber.here}
                  place={current.scope.name}
                  link={current.chamber.link}
                  size={current.chamber.size}
                  {change}
                />
              {/if}
            {:else if panel.kind === "scatter"}
              <Scatter
                contest={current}
                focus={focused}
                {picked}
                bind:hovered
              />
            {/if}
          </div>
          {#if wide.current}
            <button
              type="button"
              class="absolute inset-e-0 inset-be-0 cursor-se-resize touch-none text-muted-foreground block-4 inline-4 hover:text-foreground"
              aria-label={m.workbench_resize({ name: panel.label })}
              tabindex={-1}
              onpointerdown={(event) => drag(event, index, "resize")}
            >
              <svg
                viewBox="0 0 16 16"
                class="block-full inline-full"
                aria-hidden="true"
                ><path
                  d="M14 6 6 14M14 10l-4 4"
                  stroke="currentColor"
                  stroke-width="1.25"
                  fill="none"
                /></svg
              >
            </button>
          {/if}
        </section>
      {/each}
    </div>
  {/if}
</main>
