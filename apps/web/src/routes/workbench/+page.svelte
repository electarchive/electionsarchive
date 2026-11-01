<script lang="ts">
  import type { ViewSpec } from "@ea/view-spec";
  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { encodeId } from "@ea/view-spec";
  import ArrowLeftIcon from "@lucide/svelte/icons/arrow-left";
  import BookmarkIcon from "@lucide/svelte/icons/bookmark";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import ChevronUpIcon from "@lucide/svelte/icons/chevron-up";
  import EyeIcon from "@lucide/svelte/icons/eye";
  import LayoutGridIcon from "@lucide/svelte/icons/layout-grid";
  import LinkIcon from "@lucide/svelte/icons/link";
  import MaximizeIcon from "@lucide/svelte/icons/maximize";
  import MinimizeIcon from "@lucide/svelte/icons/minimize";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import SearchIcon from "@lucide/svelte/icons/search";
  import SettingsIcon from "@lucide/svelte/icons/settings-2";
  import XIcon from "@lucide/svelte/icons/x";
  import { createQuery, keepPreviousData } from "@tanstack/svelte-query";
  import { onMount, untrack } from "svelte";
  import { toast } from "svelte-sonner";
  import { on } from "svelte/events";
  import type { SegmentedOption } from "#lib/components/ui/segmented/index.js";
  import { copy } from "#lib/clipboard.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Dialog from "#lib/components/ui/dialog/index.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { getConfirmationContext } from "#lib/stores/confirmation.svelte.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import type { Cell, Kind, Spec } from "./grid.js";
  import Catalog from "./catalog.svelte";
  import { Desk, located } from "./desk.svelte.js";
  import Frame from "./frame.svelte";
  import {
    COLUMNS,
    GAP,
    kindOf,
    KINDS,
    ROW,
    settle,
    SIZES,
    specOf,
    standard,
    vacancy,
  } from "./grid.js";
  import Picker from "./picker.svelte";

  const { data }: { data: PageData } = $props();

  const confirmation = getConfirmationContext();
  const STACK_BELOW = 760;
  const CYCLES = [0, 15, 30, 60];
  const STORE = "workbench:board:v1";
  const MODES = $derived<Array<SegmentedOption<"view" | "arrange">>>([
    {
      value: "view",
      label: m.board_view(),
      icon: EyeIcon,
      title: `${m.board_view()} (E)`,
    },
    {
      value: "arrange",
      label: m.board_arrange(),
      icon: LayoutGridIcon,
      title: `${m.board_arrange()} (E)`,
    },
  ]);

  let spec = $state<ViewSpec | null>(untrack(() => normalize(data.spec)));
  const desk = new Desk(() => spec);
  let isFresh = $state(false);
  let current = $state(0);
  let density = $state<"compact" | "cozy">("compact");
  let cycle = $state(0);
  let width = $state(0);
  let preview = $state<{ id: string; cell: Cell } | null>(null);
  let isArranging = $state(false);
  let isPresenting = $state(false);
  let isAdding = $state(false);
  let isPicking = $state(false);
  let isSaving = $state(false);
  let isShortcuts = $state(false);
  let target = $state<{ x: number; y: number } | null>(null);
  let hover = $state<{ x: number; y: number } | null>(null);
  let renaming = $state<{ index: number; draft: string } | null>(null);
  let visibility = $state<"private" | "unlisted">(
    untrack(() =>
      data.view?.visibility === "unlisted" ? "unlisted" : "private",
    ),
  );
  let root = $state<HTMLElement>();
  const ids = new WeakMap<Spec, string>();
  let made = 0;

  let loaded = untrack(() => data.spec);

  $effect(() => {
    const next = data.spec;
    const shared = data.view;
    if (next === loaded) {
      return;
    }
    loaded = next;
    untrack(() => {
      spec = normalize(next);
      visibility = shared?.visibility === "unlisted" ? "unlisted" : "private";
      desk.picked.clear();
      desk.focus = null;
      current = 0;
    });
  });

  $effect(() => {
    if (data.isMissing) {
      toast.error(m.workbench_missing());
    }
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

  const main = createQuery(() => ({
    ...orpc.contests.get.queryOptions({ input: desk.input(undefined) }),
    enabled: spec != null,
    placeholderData: keepPreviousData,
  }));
  const contest = $derived(spec == null ? null : (main.data ?? null));

  $effect(() => {
    const found = main.data;
    if (!isFresh || spec == null || found == null || main.isPlaceholderData) {
      return;
    }
    untrack(() => {
      if (spec != null) {
        spec.panels = standard({
          hasChamber: found.chamber != null,
          hasPlaces: (found.places?.rows.length ?? 0) > 1,
        });
        spec.pages = undefined;
        isFresh = false;
      }
    });
  });

  const pages = $derived(spec?.pages ?? [m.board_page_overview()]);
  const shown = $derived(
    (spec?.panels ?? []).filter((panel) => (panel.page ?? 0) === current),
  );
  const column = $derived(Math.max(1, (width - GAP * (COLUMNS - 1)) / COLUMNS));
  const isStacked = $derived(width > 0 && width < STACK_BELOW);
  const isFrozen = $derived(!isArranging || isPresenting || isStacked);
  const layout = $derived.by(() => {
    const cells = shown.map((panel) => cellOf(panel));
    const found = preview;
    if (found == null) {
      return cells;
    }
    const at = shown.findIndex((panel) => idOf(panel) === found.id);
    if (at === -1) {
      return cells;
    }
    cells[at] = found.cell;
    return settle(cells, at);
  });
  const rows = $derived.by(() => {
    let found = 0;
    for (const cell of layout) {
      found = Math.max(found, cell.y + cell.h);
    }
    return found;
  });
  const isEmpty = $derived(shown.length === 0);
  const tail = $derived(isFrozen && !isEmpty ? 0 : 4);
  const ghost = $derived(
    hover == null
      ? null
      : { x: Math.min(hover.x, COLUMNS - 4), y: hover.y, w: 4, h: 3 },
  );
  const stacked = $derived(
    shown
      .map((panel, index) => ({ panel, index }))
      .toSorted(
        (a, b) =>
          (layout[a.index]?.y ?? 0) - (layout[b.index]?.y ?? 0) ||
          (layout[a.index]?.x ?? 0) - (layout[b.index]?.x ?? 0),
      ),
  );
  const kinds = $derived(
    KINDS.filter(
      (kind) =>
        (kind !== "hemicycle" || contest?.chamber != null) &&
        (contest == null ||
          (contest.places?.rows.length ?? 0) > 1 ||
          ["summary", "bar", "hemicycle", "note"].includes(kind)),
    ),
  );
  const levels = $derived(
    (contest?.places?.levels ?? []).map((level) => ({
      value: level.code,
      label: level.noun,
    })),
  );
  const compare = $derived.by(() => {
    const found = spec?.data.compare;
    if (found == null) {
      return "off";
    }
    return "previous" in found
      ? "previous"
      : (contest?.target?.path ?? found.contests[0] ?? "off");
  });
  const targets = $derived(
    (contest?.targets ?? []).map((entry) => ({
      value: entry.value,
      label: entry.label,
      date: entry.detail,
    })),
  );
  const standing = $derived(
    (contest?.rows ?? [])
      .filter((row) => row.current != null)
      .toSorted((a, b) => (b.current?.share ?? 0) - (a.current?.share ?? 0))
      .slice(0, 3),
  );
  const examples = $derived(
    data.examples.map((example) => ({
      id: example.id,
      label: example.name,
      detail:
        example.date == null
          ? example.election
          : `${example.election ?? ""}, ${date(example.date)}`,
    })),
  );

  function normalize(next: ViewSpec | null) {
    if (next == null) {
      return null;
    }
    const copied: ViewSpec = structuredClone(next);
    const placed = new Map<number, Array<Cell>>();
    for (const panel of copied.panels) {
      const at = Math.min(panel.page ?? 0, (copied.pages?.length ?? 1) - 1);
      panel.page = at === 0 ? undefined : at;
      const cells = placed.get(at) ?? [];
      if (panel.area == null) {
        const size = SIZES[kindOf(panel)];
        panel.area = {
          ...vacancy(cells, size.w, size.h),
          w: size.w,
          h: size.h,
        };
      }
      cells.push(panel.area);
      placed.set(at, cells);
    }
    return copied;
  }

  function idOf(panel: Spec) {
    let found = ids.get(panel);
    if (found == null) {
      made += 1;
      found = `p${made}`;
      ids.set(panel, found);
    }
    return found;
  }

  function cellOf(panel: Spec): Cell {
    return panel.area ?? { x: 0, y: 0, w: 6, h: 8 };
  }

  function commit(index: number, cell: Cell) {
    const cells = shown.map((panel) => cellOf(panel));
    cells[index] = cell;
    const settled = settle(cells, index);
    for (const [at, panel] of shown.entries()) {
      const next = settled[at];
      if (next != null) {
        panel.area = { x: next.x, y: next.y, w: next.w, h: next.h };
      }
    }
    preview = null;
  }

  function compact() {
    const cells = settle(shown.map((panel) => cellOf(panel)));
    for (const [at, panel] of shown.entries()) {
      const next = cells[at];
      if (next != null) {
        panel.area = next;
      }
    }
  }

  function remove(panel: Spec) {
    if (spec == null) {
      return;
    }
    spec.panels = spec.panels.filter((entry) => entry !== panel);
    compact();
  }

  function duplicate(panel: Spec) {
    if (spec == null) {
      return;
    }
    const area = cellOf(panel);
    const spot = vacancy(
      shown.map((entry) => cellOf(entry)),
      area.w,
      area.h,
    );
    spec.panels.push({ ...$state.snapshot(panel), area: { ...area, ...spot } });
  }

  function insert(at: { x: number; y: number } | null) {
    target = at;
    isArranging = true;
    isAdding = true;
  }

  function cellAt(event: MouseEvent) {
    const grid = event.currentTarget;
    if (isFrozen || !(grid instanceof HTMLElement) || event.target !== grid) {
      return null;
    }
    const box = grid.getBoundingClientRect();
    return {
      x: Math.max(
        0,
        Math.min(
          COLUMNS - 1,
          Math.floor((event.clientX - box.left) / (column + GAP)),
        ),
      ),
      y: Math.max(0, Math.floor((event.clientY - box.top) / (ROW + GAP))),
    };
  }

  function add(kind: Kind) {
    if (spec == null) {
      return;
    }
    const size = SIZES[kind];
    const w = Math.min(size.w, COLUMNS);
    const cells = shown.map((panel) => cellOf(panel));
    const spot =
      target == null
        ? vacancy(cells, w, size.h)
        : { x: Math.min(target.x, COLUMNS - w), y: target.y };
    const entry: Spec = {
      ...specOf(kind),
      area: { ...spot, w, h: size.h },
      page: current === 0 ? undefined : current,
    };
    spec.panels.push(entry);
    const added = spec.panels.at(-1);
    const at = shown.findIndex((panel) => panel === added);
    if (at !== -1) {
      commit(at, { ...spot, w, h: size.h });
    }
    isAdding = false;
    target = null;
    hover = null;
    const id = added == null ? null : idOf(added);
    requestAnimationFrame(() => {
      root
        ?.querySelector(`[data-panel="${id}"]`)
        ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }

  function pick(id: string) {
    const contests = [encodeId("ct", id)];
    if (spec == null) {
      spec = {
        version: 1,
        data: {
          contests,
          measure: "votes",
          channel: "all",
          compare: { previous: true },
        },
        filters: [],
        panels: [],
      };
      isFresh = true;
    } else {
      spec.data = { ...spec.data, contests, level: undefined };
      if (spec.panels.length === 0) {
        isFresh = true;
      }
    }
    desk.focus = null;
    desk.picked.clear();
    current = 0;
  }

  function go(index: number) {
    if (index >= 0 && index < pages.length) {
      current = index;
      preview = null;
    }
  }

  function shift(map: (page: number) => number | null) {
    if (spec == null) {
      return;
    }
    const kept: Array<Spec> = [];
    for (const panel of spec.panels) {
      const next = map(panel.page ?? 0);
      if (next != null) {
        panel.page = next === 0 ? undefined : next;
        kept.push(panel);
      }
    }
    spec.panels = kept;
  }

  function addPage() {
    if (spec == null || pages.length >= 12) {
      return;
    }
    spec.pages = [...pages, m.board_page_new()];
    go(pages.length - 1);
  }

  function duplicatePage(index: number) {
    if (spec == null || pages.length >= 12) {
      return;
    }
    const copies = spec.panels
      .filter((panel) => (panel.page ?? 0) === index)
      .map((panel) => $state.snapshot(panel));
    shift((at) => (at > index ? at + 1 : at));
    for (const panel of copies) {
      spec.panels.push({ ...panel, page: index + 1 });
    }
    spec.pages = pages.toSpliced(index + 1, 0, pages[index] ?? "");
    go(index + 1);
  }

  function movePage(index: number, step: number) {
    const other = index + step;
    if (spec == null || other < 0 || other >= pages.length) {
      return;
    }
    shift((at) => (at === index ? other : at === other ? index : at));
    const names = [...pages];
    names[index] = pages[other] ?? "";
    names[other] = pages[index] ?? "";
    spec.pages = names;
    go(other);
  }

  async function removePage(index: number) {
    if (spec == null || pages.length === 1) {
      return;
    }
    const isSure = await confirmation.confirm({
      title: m.board_page_remove(),
      description: pages[index] ?? "",
      confirmText: m.board_page_remove(),
      variant: "destructive",
    });
    if (!isSure) {
      return;
    }
    shift((at) => (at === index ? null : at > index ? at - 1 : at));
    const names = pages.toSpliced(index, 1);
    spec.pages = names.length === 1 ? undefined : names;
    go(Math.min(index, names.length - 1));
  }

  function renamePage() {
    if (renaming == null || spec == null) {
      return;
    }
    const name = renaming.draft.trim().slice(0, 40);
    if (name !== "") {
      spec.pages = pages.with(renaming.index, name);
    }
    renaming = null;
  }

  async function reset() {
    const isSure = await confirmation.confirm({
      title: m.board_reset(),
      description: m.board_reset_body(),
      confirmText: m.board_reset(),
      variant: "destructive",
    });
    if (isSure && spec != null) {
      spec.panels = [];
      spec.pages = undefined;
      isFresh = true;
      current = 0;
    }
  }

  async function share() {
    try {
      await copy(location.href);
      toast.success(m.workbench_copied());
    } catch {
      toast.error(m.workbench_copy());
    }
  }

  async function present() {
    if (document.fullscreenElement != null) {
      await document.exitFullscreen();
      return;
    }
    try {
      await root?.requestFullscreen();
    } catch {
      isPresenting = !isPresenting;
    }
  }

  function shortcut(event: KeyboardEvent) {
    const element = event.target;
    if (
      event.metaKey ||
      event.ctrlKey ||
      event.altKey ||
      (element instanceof HTMLElement &&
        (element.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName))) ||
      document.querySelector("[role=dialog],[role=alertdialog],[role=menu]") !=
        null
    ) {
      return;
    }
    const digit = Number(event.key);
    if (event.key === "/") {
      isPicking = true;
    } else if (spec == null) {
      return;
    } else if (Number.isInteger(digit) && digit >= 1 && digit <= 9) {
      go(digit - 1);
    } else if (event.key === "]") {
      go((current + 1) % pages.length);
    } else if (event.key === "[") {
      go((current - 1 + pages.length) % pages.length);
    } else if (event.key === "f") {
      void present();
    } else if (event.key === "e" && !isStacked && !isPresenting) {
      isArranging = !isArranging;
    } else if (event.key === "a" && !isStacked && !isPresenting) {
      insert(null);
    } else if (event.key === "Escape" && desk.picked.size > 0) {
      desk.picked.clear();
    } else if (event.key === "?") {
      isShortcuts = true;
    } else {
      return;
    }
    event.preventDefault();
  }

  onMount(() => {
    try {
      const stored: unknown = JSON.parse(localStorage.getItem(STORE) ?? "{}");
      if (typeof stored === "object" && stored != null) {
        if ("density" in stored && stored.density === "cozy") {
          density = "cozy";
        }
        if (
          "cycle" in stored &&
          typeof stored.cycle === "number" &&
          CYCLES.includes(stored.cycle)
        ) {
          cycle = stored.cycle;
        }
      }
    } catch {
      density = "compact";
    }
    return on(document, "fullscreenchange", () => {
      isPresenting = document.fullscreenElement != null;
    });
  });

  $effect(() => {
    const saved = JSON.stringify({ density, cycle });
    try {
      localStorage.setItem(STORE, saved);
    } catch {}
  });

  $effect(() => {
    if (!isPresenting || cycle === 0 || pages.length < 2) {
      return;
    }
    const timer = setInterval(() => {
      go((current + 1) % pages.length);
    }, cycle * 1000);
    return () => clearInterval(timer);
  });
</script>

<svelte:head>
  <title
    >{spec?.title ?? contest?.name ?? m.workbench_title()} | {m.wordmark()}</title
  >
  <meta name="robots" content="noindex" />
</svelte:head>

<svelte:window onkeydown={shortcut} />

<div
  bind:this={root}
  class={cn("fixed inset-0 z-40 flex flex-col overflow-y-auto bg-muted", {
    "text-[0.8125rem]": density !== "cozy",
    "text-[0.9375rem]": density === "cozy",
  })}
>
  <header
    class="sticky inset-bs-0 z-40 flex flex-wrap items-center gap-x-3 gap-y-1 border-be border-border bg-background px-3 py-1.5 text-[0.8125rem]"
  >
    {#if !isPresenting}
      <a
        href={contest?.path == null
          ? resolve("/")
          : resolve("/[...path]", { path: contest.path })}
        class="-me-1 grid shrink-0 place-content-center rounded-xs text-muted-foreground block-7 inline-7 hover:bg-muted hover:text-foreground"
        aria-label={contest?.path == null ? m.wordmark() : m.workbench_open()}
        title={contest?.path == null ? m.wordmark() : m.workbench_open()}
      >
        <ArrowLeftIcon class="block-4 inline-4" aria-hidden="true" />
      </a>
    {/if}
    <div class="grid leading-tight min-inline-0">
      <h1 class="min-inline-0">
        <button
          type="button"
          class="flex items-center gap-1 rounded-xs font-semibold max-inline-[44ch] hover:bg-muted"
          title="{m.workbench_contest_placeholder()} (/)"
          onclick={() => (isPicking = true)}
        >
          <span class="truncate"
            >{spec?.title ??
              (contest == null ? m.workbench_title() : contest.name)}</span
          >
          <ChevronDownIcon
            class="shrink-0 text-muted-foreground block-3.5 inline-3.5"
            aria-hidden="true"
          />
        </button>
      </h1>
      <p
        class="flex items-center gap-2 truncate text-[0.6875rem] text-muted-foreground"
      >
        {#if contest != null}
          <span class="truncate"
            >{contest.election.name}{#if contest.election.date != null}, {date(
                contest.election.date,
              )}{/if}</span
          >
        {:else}
          <span>{m.workbench_lede_short()}</span>
        {/if}
        {#if spec != null && data.view?.isOwner === true}
          <span
            class={cn("shrink-0", { "font-semibold text-foreground": isDirty })}
            aria-live="polite"
            >{isDirty
              ? m.workbench_status_dirty()
              : m.workbench_status_saved()}</span
          >
        {:else if data.view != null && !data.view.isOwner}
          <span class="shrink-0">{m.workbench_shared_short()}</span>
        {/if}
      </p>
    </div>
    {#if standing.length > 0}
      <ul
        class="hidden items-center gap-3 border-s border-border ps-3 lg:flex"
        aria-label={m.workbench_panel_results()}
      >
        {#each standing as row (row.key)}
          {const color = $derived(swatch(row.color, row.key))}
          <li class="grid gap-0.5 leading-none">
            <span class="flex items-baseline gap-1.5 text-[0.6875rem]">
              <span class="truncate font-semibold max-inline-[16ch]"
                >{row.short ?? row.label}</span
              >
              <span
                class="font-figures text-[0.8125rem] font-[760] tabular-nums"
                style:color>{percent(row.current?.share ?? 0)}</span
              >
            </span>
            <span
              class="flex overflow-hidden rounded-[1px] bg-accent block-1 inline-full"
              aria-hidden="true"
            >
              <span
                style:inline-size="{(row.current?.share ?? 0) * 100}%"
                style:background-color={color}
              ></span>
            </span>
          </li>
        {/each}
      </ul>
    {/if}
    {#if spec != null}
      <div class="flex flex-wrap items-center gap-1">
        {#if levels.length > 1}
          <Segmented
            options={levels}
            value={contest?.places?.level ?? undefined}
            onValueChange={(next: string) => {
              if (spec != null) {
                spec.data.level = next;
              }
              desk.picked.clear();
            }}
            label={m.workbench_level()}
          />
        {/if}
        <DropdownMenu.Root>
          <DropdownMenu.Trigger
            class="flex items-center gap-1 rounded-sm px-2 text-[0.8125rem] font-medium block-8 hover:bg-muted [&_svg]:block-3.5 [&_svg]:inline-3.5"
          >
            <span class="text-muted-foreground"
              >{m.workbench_compare_with()}</span
            >
            <span class="truncate max-inline-[18ch]"
              >{compare === "off"
                ? m.workbench_compare_off()
                : compare === "previous"
                  ? (contest?.target?.year ?? m.workbench_compare_previous())
                  : (targets.find((entry) => entry.value === compare)?.label ??
                    contest?.target?.name ??
                    m.workbench_compare_previous())}</span
            >
            <ChevronDownIcon class="text-muted-foreground" aria-hidden="true" />
          </DropdownMenu.Trigger>
          <DropdownMenu.Content align="start" class="inline-72">
            <DropdownMenu.RadioGroup
              value={compare}
              onValueChange={(value: string) => {
                if (spec == null) {
                  return;
                }
                if (value === "off" || value === "previous") {
                  spec.data.compare =
                    value === "off" ? undefined : { previous: true };
                  return;
                }
                void located(value).then(
                  (id) => {
                    if (spec != null) {
                      spec.data.compare = { contests: [encodeId("ct", id)] };
                    }
                  },
                  () => toast.error(m.workbench_panel_error()),
                );
              }}
            >
              <DropdownMenu.RadioItem value="off"
                >{m.workbench_compare_off()}</DropdownMenu.RadioItem
              >
              <DropdownMenu.RadioItem value="previous"
                >{m.workbench_compare_previous()}</DropdownMenu.RadioItem
              >
              {#if targets.length > 0}
                <DropdownMenu.Separator />
                {#each targets as entry (entry.value)}
                  <DropdownMenu.RadioItem
                    value={entry.value}
                    class="grid gap-0.5"
                  >
                    <span class="truncate">{entry.label}</span>
                    {#if entry.date != null}
                      <span class="text-xs text-muted-foreground"
                        >{date(entry.date)}</span
                      >
                    {/if}
                  </DropdownMenu.RadioItem>
                {/each}
              {/if}
            </DropdownMenu.RadioGroup>
          </DropdownMenu.Content>
        </DropdownMenu.Root>
        {#if desk.picked.size > 0}
          <Button
            variant="secondary"
            size="sm"
            class="gap-1 px-2"
            title="{m.workbench_clear()} (Esc)"
            onclick={() => desk.picked.clear()}
          >
            {m.workbench_selected({ count: desk.picked.size })}
            <XIcon aria-hidden="true" />
            <span class="sr-only">{m.workbench_clear()}</span>
          </Button>
        {/if}
      </div>
    {/if}
    <div class="ms-auto flex items-center gap-1">
      {#if spec != null && !isPresenting && !isStacked}
        <Segmented
          class="me-1"
          options={MODES}
          value={isArranging ? "arrange" : "view"}
          label={m.board_mode()}
          onValueChange={(next: "view" | "arrange") => {
            isArranging = next === "arrange";
            hover = null;
          }}
        />
        {#if isArranging}
          <Button
            size="icon"
            variant="ghost"
            aria-label={m.board_add_title()}
            title="{m.board_add_title()} (A)"
            onclick={() => insert(null)}
          >
            <PlusIcon />
          </Button>
        {/if}
      {/if}
      {#if !isPresenting}
        <DropdownMenu.Root>
          <DropdownMenu.Trigger
            class="grid place-content-center rounded-sm text-muted-foreground block-8 inline-8 hover:bg-muted hover:text-foreground"
            aria-label={m.workbench_views()}
            title={m.workbench_views()}
          >
            <BookmarkIcon class="block-4 inline-4" aria-hidden="true" />
          </DropdownMenu.Trigger>
          <DropdownMenu.Content align="end" class="max-inline-80 min-inline-56">
            <DropdownMenu.Label>{m.workbench_views()}</DropdownMenu.Label>
            {#if data.views.length === 0}
              <p class="px-2 py-1.5 text-sm text-muted-foreground">
                {m.workbench_views_empty()}
              </p>
            {/if}
            {#each data.views as view (view.id)}
              <DropdownMenu.Item
                class={cn("grid gap-0.5", {
                  "bg-accent": view.id === data.view?.id,
                })}
                onSelect={async () =>
                  goto(resolve(`/workbench?view=${view.id}`))}
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
            <DropdownMenu.Item
              onSelect={async () => goto(resolve("/workbench"))}
            >
              <PlusIcon />{m.workbench_new()}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Root>
        {#if spec != null}
          <Popover.Root bind:open={isSaving}>
            <Popover.Trigger>
              {#snippet child({ props })}
                <Button
                  {...props}
                  size="sm"
                  variant={isDirty || data.view?.isOwner !== true
                    ? "default"
                    : "ghost"}
                >
                  {data.view == null
                    ? m.workbench_save()
                    : data.view.isOwner
                      ? m.workbench_save_changes()
                      : m.workbench_save_copy()}
                </Button>
              {/snippet}
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
                    oninput={(
                      event: Event & { currentTarget: HTMLInputElement },
                    ) => {
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
                  <Segmented
                    options={[
                      { value: "private", label: m.workbench_private() },
                      { value: "unlisted", label: m.workbench_unlisted() },
                    ]}
                    bind:value={visibility}
                    label={m.workbench_visibility()}
                    fill
                  />
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
          <Button
            size="icon"
            variant="ghost"
            aria-label={m.workbench_copy()}
            title={m.workbench_copy()}
            onclick={share}
          >
            <LinkIcon />
          </Button>
        {/if}
      {/if}
      {#if spec != null}
        <Button
          size="sm"
          variant="ghost"
          title="{isPresenting
            ? m.board_present_exit()
            : m.board_present()} (F)"
          onclick={present}
        >
          {#if isPresenting}<MinimizeIcon
            />{m.board_present_exit()}{:else}<MaximizeIcon
            />{m.board_present()}{/if}
        </Button>
      {/if}
      {#if !isPresenting}
        <DropdownMenu.Root>
          <DropdownMenu.Trigger
            class="grid place-content-center rounded-sm text-muted-foreground block-8 inline-8 hover:bg-muted hover:text-foreground"
            aria-label={m.board_settings()}
            title={m.board_settings()}
          >
            <SettingsIcon class="block-4 inline-4" aria-hidden="true" />
          </DropdownMenu.Trigger>
          <DropdownMenu.Content align="end" class="inline-60">
            <DropdownMenu.Label>{m.board_density()}</DropdownMenu.Label>
            <DropdownMenu.RadioGroup
              value={density}
              onValueChange={(value) => {
                density = value === "cozy" ? "cozy" : "compact";
              }}
            >
              <DropdownMenu.RadioItem value="compact"
                >{m.board_density_compact()}</DropdownMenu.RadioItem
              >
              <DropdownMenu.RadioItem value="cozy"
                >{m.board_density_cozy()}</DropdownMenu.RadioItem
              >
            </DropdownMenu.RadioGroup>
            <DropdownMenu.Separator />
            <DropdownMenu.Label>{m.board_cycle()}</DropdownMenu.Label>
            <DropdownMenu.RadioGroup
              value={String(cycle)}
              onValueChange={(value) => {
                cycle = Number(value);
              }}
            >
              {#each CYCLES as seconds (seconds)}
                <DropdownMenu.RadioItem value={String(seconds)}
                  >{seconds === 0
                    ? m.board_cycle_off()
                    : m.board_cycle_seconds({
                        seconds,
                      })}</DropdownMenu.RadioItem
                >
              {/each}
            </DropdownMenu.RadioGroup>
            <DropdownMenu.Separator />
            <DropdownMenu.Item onSelect={() => (isShortcuts = true)}>
              {m.board_shortcuts()}
              <DropdownMenu.Shortcut>?</DropdownMenu.Shortcut>
            </DropdownMenu.Item>
            {#if spec != null}
              <DropdownMenu.Item variant="destructive" onSelect={reset}>
                {m.board_reset()}
              </DropdownMenu.Item>
            {/if}
          </DropdownMenu.Content>
        </DropdownMenu.Root>
      {/if}
    </div>
  </header>
  {#if spec == null}
    <main class="grid flex-1 content-start justify-items-center p-3 pbs-[8dvh]">
      <section class="grid gap-4 inline-full max-inline-3xl">
        <div class="grid gap-1.5">
          <h2 class="text-2xl/tight font-[760] text-balance">
            {m.workbench_title()}
          </h2>
          <p class="text-[0.9375rem] text-muted-foreground max-inline-[60ch]">
            {m.workbench_lede()}
          </p>
        </div>
        <button
          type="button"
          class="flex items-center gap-2 rounded-sm border border-input bg-background px-3 text-start text-muted-foreground block-11 hover:border-foreground/40"
          onclick={() => (isPicking = true)}
        >
          <SearchIcon class="shrink-0 block-4 inline-4" aria-hidden="true" />
          <span class="flex-1 truncate"
            >{m.workbench_contest_placeholder()}</span
          >
          <kbd
            class="rounded-xs border border-border bg-muted px-1.5 py-0.5 font-figures text-xs"
            >/</kbd
          >
        </button>
        <div class="grid gap-1.5">
          <h3 class="text-xs font-semibold text-muted-foreground">
            {m.workbench_examples()}
          </h3>
          <ul
            class="grid gap-px overflow-hidden rounded-sm bg-border ring-1 ring-border sm:grid-cols-2 lg:grid-cols-3"
          >
            {#each data.examples as example (example.id)}
              <li class="grid">
                <button
                  type="button"
                  class="grid content-start gap-1 bg-background p-3 text-start hover:bg-muted focus-visible:relative"
                  onclick={() => pick(example.id)}
                >
                  <span class="text-[1.0625rem]/tight font-[720] text-balance"
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
        </div>
      </section>
    </main>
  {:else}
    {#if isStacked}
      <p class="px-3 pbs-2 text-[0.75rem] text-muted-foreground">
        {m.board_stacked()}
      </p>
    {/if}
    <main class="flex-1 p-1.5 sm:p-2">
      <div
        bind:clientWidth={width}
        class={cn({
          relative: !isStacked,
          "grid gap-1.5": isStacked,
          "cursor-copy": !isFrozen,
        })}
        style:block-size={isStacked
          ? undefined
          : `${(rows + tail) * (ROW + GAP)}px`}
        style:background-image={isFrozen
          ? undefined
          : `repeating-linear-gradient(to right, color-mix(in oklab, var(--foreground) 5%, transparent) 0 ${column}px, transparent ${column}px ${column + GAP}px)`}
        role="presentation"
        onpointermove={(event: PointerEvent) => (hover = cellAt(event))}
        onpointerleave={() => (hover = null)}
        onclick={(event: MouseEvent) => {
          const spot = cellAt(event);
          if (spot != null) {
            insert({ x: Math.min(spot.x, COLUMNS - 4), y: spot.y });
          }
        }}
      >
        {#if width > 0}
          {#if preview != null && !isStacked}
            {const at = $derived(
              shown.findIndex((panel) => idOf(panel) === preview?.id),
            )}
            {const spot = $derived(layout[at])}
            {#if spot != null}
              <div
                class="pointer-events-none absolute inset-s-0 inset-bs-0 rounded-sm border border-dashed border-foreground/40 bg-foreground/5 transition-transform duration-100 motion-reduce:transition-none"
                style:transform="translate({spot.x * (column + GAP)}px, {spot.y *
                  (ROW + GAP)}px)"
                style:inline-size="{spot.w * column + (spot.w - 1) * GAP}px"
                style:block-size="{spot.h * ROW + (spot.h - 1) * GAP}px"
                aria-hidden="true"
              ></div>
            {/if}
          {:else if ghost != null && !isFrozen}
            <div
              class="pointer-events-none absolute inset-s-0 inset-bs-0 grid place-content-center rounded-sm border border-dashed border-foreground/50 bg-background/70 text-muted-foreground"
              style:transform="translate({ghost.x * (column + GAP)}px, {ghost.y *
                (ROW + GAP)}px)"
              style:inline-size="{ghost.w * column + (ghost.w - 1) * GAP}px"
              style:block-size="{ghost.h * ROW + (ghost.h - 1) * GAP}px"
              aria-hidden="true"
            >
              <PlusIcon class="block-4 inline-4" />
            </div>
          {/if}
          {#each isStacked ? stacked : shown.map( (panel, index) => ({ panel, index }) ) as entry (idOf(entry.panel))}
            {const id = $derived(idOf(entry.panel))}
            <Frame
              {id}
              {desk}
              panel={entry.panel}
              cell={preview?.id === id
                ? cellOf(entry.panel)
                : (layout[entry.index] ?? cellOf(entry.panel))}
              {column}
              isLocked={isFrozen}
              {isStacked}
              isActive={preview?.id === id}
              onPreview={(cell: Cell) => {
                preview = { id, cell };
              }}
              onCommit={(cell: Cell) => commit(entry.index, cell)}
              onRemove={() => remove(entry.panel)}
              onDuplicate={() => duplicate(entry.panel)}
              onChange={(patch: {
                contest?: string;
                title?: string;
                text?: string;
              }) => {
                Object.assign(entry.panel, patch);
              }}
            />
          {/each}
          {#if tail > 0 && !isPresenting && !isStacked}
            <button
              type="button"
              class="absolute inset-s-0 flex items-center justify-center gap-1.5 rounded-sm border border-dashed border-foreground/25 text-muted-foreground inline-full hover:border-foreground/50 hover:bg-background/60 hover:text-foreground"
              style:inset-block-start="{rows * (ROW + GAP)}px"
              style:block-size="{3 * ROW + 2 * GAP}px"
              onpointermove={() => (hover = null)}
              onclick={() => insert({ x: 0, y: rows })}
            >
              <PlusIcon class="block-4 inline-4" aria-hidden="true" />
              {m.board_add_title()}
            </button>
          {/if}
        {/if}
      </div>
    </main>
    <footer
      class="sticky inset-be-0 z-30 flex items-stretch gap-3 border-bs border-border bg-background pe-3 text-[0.75rem]"
    >
      <nav
        class="flex items-stretch overflow-x-auto min-inline-0"
        aria-label={m.board_pages()}
      >
        {#each pages as name, index (index)}
          {const isCurrent = $derived(index === current)}
          <div
            class={cn("relative -mbs-px flex shrink-0 items-center border-e", {
              "border-border bg-muted font-semibold text-foreground": isCurrent,
              "border-border/60 text-muted-foreground hover:bg-muted/50 hover:text-foreground":
                !isCurrent,
            })}
          >
            <button
              type="button"
              class={cn("py-1.5 whitespace-nowrap", {
                "ps-3 pe-1": isCurrent && !isPresenting,
                "px-3": !isCurrent || isPresenting,
              })}
              aria-current={isCurrent ? "page" : undefined}
              title={index < 9 ? `${name} (${index + 1})` : name}
              onclick={() => go(index)}
              ondblclick={() => {
                if (!isPresenting) {
                  renaming = { index, draft: name };
                }
              }}
            >
              {name}
            </button>
            {#if isCurrent && !isPresenting}
              <DropdownMenu.Root>
                <DropdownMenu.Trigger
                  class="me-1 grid place-content-center rounded-xs text-muted-foreground block-5 inline-5 hover:bg-background hover:text-foreground"
                  aria-label={m.board_page_actions()}
                  title={m.board_page_actions()}
                >
                  <ChevronUpIcon
                    class="block-3.5 inline-3.5"
                    aria-hidden="true"
                  />
                </DropdownMenu.Trigger>
                <DropdownMenu.Content
                  side="top"
                  align="start"
                  class="inline-52"
                >
                  <DropdownMenu.Item
                    onSelect={() =>
                      (renaming = { index: current, draft: name })}
                  >
                    {m.board_page_rename()}
                  </DropdownMenu.Item>
                  <DropdownMenu.Item
                    disabled={pages.length >= 12}
                    onSelect={() => duplicatePage(current)}
                  >
                    {m.board_page_duplicate()}
                  </DropdownMenu.Item>
                  <DropdownMenu.Item
                    disabled={current === 0}
                    onSelect={() => movePage(current, -1)}
                  >
                    {m.board_page_left()}
                  </DropdownMenu.Item>
                  <DropdownMenu.Item
                    disabled={current === pages.length - 1}
                    onSelect={() => movePage(current, 1)}
                  >
                    {m.board_page_right()}
                  </DropdownMenu.Item>
                  <DropdownMenu.Separator />
                  <DropdownMenu.Item
                    variant="destructive"
                    disabled={pages.length === 1}
                    onSelect={async () => removePage(current)}
                  >
                    {m.board_page_remove()}
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Root>
            {/if}
            {#if isCurrent && isPresenting && cycle > 0 && pages.length > 1}
              {#key current}
                <span
                  class="rotation absolute inset-x-0 inset-be-0 bg-foreground block-0.5 motion-reduce:hidden"
                  style:animation-duration="{cycle}s"
                  aria-hidden="true"
                ></span>
              {/key}
            {/if}
          </div>
        {/each}
        {#if !isPresenting && pages.length < 12}
          <button
            type="button"
            class="grid shrink-0 place-content-center text-muted-foreground inline-8 hover:bg-muted hover:text-foreground"
            aria-label={m.board_page_add()}
            title={m.board_page_add()}
            onclick={addPage}
          >
            <PlusIcon class="block-3.5 inline-3.5" aria-hidden="true" />
          </button>
        {/if}
      </nav>
      <p
        class="ms-auto hidden items-center truncate text-muted-foreground min-inline-0 md:flex"
      >
        {isFrozen ? m.workbench_board_hint() : m.board_arrange_hint()}
      </p>
    </footer>
  {/if}
</div>

<Picker
  bind:open={isPicking}
  title={m.workbench_contest()}
  quick={examples}
  quickLabel={m.workbench_examples()}
  onPick={pick}
/>

<Catalog bind:open={isAdding} {kinds} onPick={(kind: Kind) => add(kind)} />

<Dialog.Root
  open={renaming != null}
  onOpenChange={(open: boolean) => {
    if (!open) {
      renaming = null;
    }
  }}
>
  <Dialog.Content class="max-inline-sm">
    <Dialog.Header>
      <Dialog.Title>{m.board_page_rename()}</Dialog.Title>
    </Dialog.Header>
    {#if renaming != null}
      <form
        class="grid gap-3"
        onsubmit={(event) => {
          event.preventDefault();
          renamePage();
        }}
      >
        <Input
          bind:value={renaming.draft}
          aria-label={m.board_page_name()}
          maxlength={40}
        />
        <Dialog.Footer>
          <Button type="submit">{m.board_page_rename()}</Button>
        </Dialog.Footer>
      </form>
    {/if}
  </Dialog.Content>
</Dialog.Root>

<Dialog.Root bind:open={isShortcuts}>
  <Dialog.Content class="max-inline-md">
    <Dialog.Header>
      <Dialog.Title>{m.board_shortcuts()}</Dialog.Title>
    </Dialog.Header>
    <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
      {#each [["/", m.workbench_shortcut_contest()], ["1–9", m.board_shortcut_pages()], ["[ ]", m.board_shortcut_next()], ["E", m.board_shortcut_arrange()], ["A", m.board_shortcut_add()], ["F", m.board_shortcut_present()], ["Esc", m.workbench_clear()], ["← → ↑ ↓", m.board_shortcut_move()], ["Shift ← → ↑ ↓", m.board_shortcut_resize()]] as [keys, text] (keys)}
        <dt>
          <kbd
            class="rounded-xs border border-border bg-muted px-1.5 py-0.5 font-figures text-xs"
            >{keys}</kbd
          >
        </dt>
        <dd>{text}</dd>
      {/each}
    </dl>
  </Dialog.Content>
</Dialog.Root>

<style>
  .rotation {
    transform-origin: left;
    animation-name: rotation;
    animation-timing-function: linear;
  }

  @keyframes rotation {
    from {
      transform: scaleX(0);
    }
    to {
      transform: scaleX(1);
    }
  }
</style>
