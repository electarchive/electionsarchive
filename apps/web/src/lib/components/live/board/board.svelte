<script lang="ts">
  import { replaceState } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowLeftIcon from "@lucide/svelte/icons/arrow-left";
  import EllipsisIcon from "@lucide/svelte/icons/ellipsis";
  import LinkIcon from "@lucide/svelte/icons/link";
  import LockIcon from "@lucide/svelte/icons/lock";
  import LockOpenIcon from "@lucide/svelte/icons/lock-open";
  import MaximizeIcon from "@lucide/svelte/icons/maximize";
  import MinimizeIcon from "@lucide/svelte/icons/minimize";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import SettingsIcon from "@lucide/svelte/icons/settings-2";
  import { onMount } from "svelte";
  import { on } from "svelte/events";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import type { Board, Cell, Kind, Panel } from "#lib/live/board/layout.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Dialog from "#lib/components/ui/dialog/index.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { KIND_TEXT } from "#lib/live/board/labels.js";
  import {
    COLUMNS,
    forget,
    GAP,
    id,
    KINDS,
    load,
    pack,
    ROW,
    save,
    settle,
    SIZES,
    standard,
    unpack,
    vacancy,
  } from "#lib/live/board/layout.js";
  import { closing } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getConfirmationContext } from "#lib/stores/confirmation.svelte.js";
  import { cn } from "#lib/utils.js";
  import Frame from "./frame.svelte";

  const { desk }: { desk: Desk } = $props();

  const confirmation = getConfirmationContext();
  const STACK_BELOW = 760;
  const CYCLES = [0, 15, 30, 60];
  const RACE_KINDS = new Set<Kind>([
    "race",
    "map",
    "chance",
    "trend",
    "counties",
  ]);
  const OFFICE_KINDS = new Set<Kind>([
    "power",
    "nation",
    "closest",
    "flips",
    "seats",
  ]);

  const feed = $derived(desk.feed);
  const key = $derived(`board:v1:${feed.election.slug}`);

  let board = $state<Board>(fresh());
  let width = $state(0);
  let preview = $state<{
    id: string;
    cell: Cell;
    mode: "move" | "resize";
  } | null>(null);
  let isLocked = $state(false);
  let isPresenting = $state(false);
  let isAdding = $state(false);
  let isShortcuts = $state(false);
  let renaming = $state<{ index: number; draft: string } | null>(null);
  let isShared = $state(false);
  let root = $state<HTMLElement>();

  const current = $derived(board.pages[board.current] ?? board.pages[0]);
  const column = $derived(Math.max(1, (width - GAP * (COLUMNS - 1)) / COLUMNS));
  const isStacked = $derived(width > 0 && width < STACK_BELOW);
  const isFrozen = $derived(isLocked || isPresenting);
  const layout = $derived.by(() => {
    const panels = current?.panels ?? [];
    const found = preview;
    if (found == null) {
      return new Map(panels.map((entry) => [entry.id, cellOf(entry)]));
    }
    return new Map(
      settle(
        panels.map((entry) =>
          entry.id === found.id ? { ...entry, ...found.cell } : entry,
        ),
        found.id,
      ).map((entry) => [entry.id, cellOf(entry)]),
    );
  });
  const rows = $derived.by(() => {
    let found = 0;
    for (const cell of layout.values()) {
      found = Math.max(found, cell.y + cell.h);
    }
    return found + (isFrozen ? 0 : 6);
  });
  const stacked = $derived(
    (current?.panels ?? []).toSorted((a, b) => a.y - b.y || a.x - b.x),
  );
  const kinds = $derived(
    KINDS.filter(
      (kind) =>
        (kind !== "nation" || feed.isNation) &&
        (kind !== "hexes" || desk.cartogram != null),
    ),
  );
  const firstClose = $derived(
    Math.min(
      ...feed.election.races.map((race) => closing(feed.election, race)),
    ),
  );
  const status = $derived(
    feed.isRehearsal
      ? "rehearsal"
      : feed.isOffline
        ? "offline"
        : feed.clock < firstClose
          ? "before"
          : "live",
  );

  function fresh() {
    return standard(desk.feed.election, {
      overview: m.board_page_overview(),
      desk: m.board_page_desk(),
      offices: {
        senate: m.board_page_senate(),
        governor: m.board_page_governor(),
        house: m.board_page_house(),
        other: m.board_results(),
      },
    });
  }

  function cellOf(entry: Panel): Cell {
    return { x: entry.x, y: entry.y, w: entry.w, h: entry.h };
  }

  function shown(entry: Panel) {
    if (preview?.id === entry.id) {
      return cellOf(entry);
    }
    return layout.get(entry.id) ?? cellOf(entry);
  }

  function commit(target: string, cell: Cell) {
    if (current == null) {
      return;
    }
    current.panels = settle(
      current.panels.map((entry) =>
        entry.id === target ? { ...entry, ...cell } : entry,
      ),
      target,
    );
    preview = null;
  }

  function remove(target: string) {
    if (current == null) {
      return;
    }
    current.panels = settle(
      current.panels.filter((entry) => entry.id !== target),
    );
  }

  function duplicate(target: string) {
    const found = current?.panels.find((entry) => entry.id === target);
    if (current == null || found == null) {
      return;
    }
    const spot = vacancy(current.panels, found.w, found.h);
    current.panels = [...current.panels, { ...found, id: id(), ...spot }];
  }

  function add(kind: Kind) {
    if (current == null) {
      return;
    }
    const size = SIZES[kind];
    const w = Math.min(size.w, COLUMNS);
    const spot = vacancy(current.panels, w, size.h);
    const entry: Panel = { id: id(), kind, ...spot, w, h: size.h };
    if (RACE_KINDS.has(kind)) {
      entry.race = null;
    }
    if (OFFICE_KINDS.has(kind)) {
      entry.office = "senate";
    }
    if (kind === "map") {
      entry.mode = "lead";
    }
    if (kind === "note") {
      entry.text = "";
    }
    current.panels = [...current.panels, entry];
    isAdding = false;
    requestAnimationFrame(() => {
      root
        ?.querySelector(`[data-panel="${entry.id}"]`)
        ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }

  function go(index: number) {
    if (index >= 0 && index < board.pages.length) {
      board.current = index;
      preview = null;
    }
  }

  function addPage() {
    if (board.pages.length >= 12) {
      return;
    }
    board.pages.push({ id: id(), name: m.board_page_new(), panels: [] });
    go(board.pages.length - 1);
  }

  function duplicatePage(index: number) {
    const found = board.pages[index];
    if (found == null || board.pages.length >= 12) {
      return;
    }
    board.pages.splice(index + 1, 0, {
      id: id(),
      name: found.name,
      panels: found.panels.map((entry) => ({ ...entry, id: id() })),
    });
    go(index + 1);
  }

  function movePage(index: number, step: number) {
    const target = index + step;
    const found = board.pages[index];
    if (found == null || target < 0 || target >= board.pages.length) {
      return;
    }
    board.pages.splice(index, 1);
    board.pages.splice(target, 0, found);
    go(target);
  }

  async function removePage(index: number) {
    const found = board.pages[index];
    if (found == null || board.pages.length === 1) {
      return;
    }
    const isSure = await confirmation.confirm({
      title: m.board_page_remove(),
      description: found.name,
      confirmText: m.board_page_remove(),
      variant: "destructive",
    });
    if (!isSure) {
      return;
    }
    board.pages.splice(index, 1);
    go(Math.min(index, board.pages.length - 1));
  }

  function renamePage() {
    if (renaming == null) {
      return;
    }
    const found = board.pages[renaming.index];
    const name = renaming.draft.trim().slice(0, 40);
    if (found != null && name !== "") {
      found.name = name;
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
    if (isSure) {
      forget(key);
      board = fresh();
    }
  }

  async function share() {
    const packed = await pack($state.snapshot(board));
    const url = new URL(page.url.href);
    url.hash = `b=${packed}`;
    try {
      await navigator.clipboard.writeText(url.toString());
      isShared = true;
      setTimeout(() => {
        isShared = false;
      }, 2000);
    } catch {
      void replaceState(url, page.state);
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
    const target = event.target;
    if (
      event.metaKey ||
      event.ctrlKey ||
      event.altKey ||
      (target instanceof HTMLElement &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) ||
      document.querySelector("[role=dialog],[role=alertdialog],[role=menu]") !=
        null
    ) {
      return;
    }
    const digit = Number(event.key);
    if (Number.isInteger(digit) && digit >= 1 && digit <= 9) {
      go(digit - 1);
    } else if (event.key === "]") {
      go((board.current + 1) % board.pages.length);
    } else if (event.key === "[") {
      go((board.current - 1 + board.pages.length) % board.pages.length);
    } else if (event.key === "p") {
      void present();
    } else if (event.key === "l") {
      isLocked = !isLocked;
    } else if (event.key === "a" && !isFrozen) {
      isAdding = true;
    } else if (event.key === "?") {
      isShortcuts = true;
    } else {
      return;
    }
    event.preventDefault();
  }

  onMount(() => {
    const stored = load(key);
    if (stored != null) {
      board = stored;
    }
    const hash = page.url.hash;
    if (hash.startsWith("#b=")) {
      void (async () => {
        const shared = await unpack(hash.slice(3));
        void replaceState(page.url.pathname + page.url.search, page.state);
        if (shared == null) {
          return;
        }
        const isSure = await confirmation.confirm({
          title: m.board_import_title(),
          description: m.board_import_body(),
          confirmText: m.board_import_accept(),
          cancelText: m.board_import_decline(),
        });
        if (isSure) {
          board = shared;
        }
      })();
    }
    const change = () => {
      isPresenting = document.fullscreenElement != null;
    };
    return on(document, "fullscreenchange", change);
  });

  $effect(() => {
    save(key, $state.snapshot(board));
  });

  $effect(() => {
    const seconds = board.cycle ?? 0;
    if (!isPresenting || seconds === 0 || board.pages.length < 2) {
      return;
    }
    const timer = setInterval(() => {
      go((board.current + 1) % board.pages.length);
    }, seconds * 1000);
    return () => clearInterval(timer);
  });
</script>

<svelte:window onkeydown={shortcut} />

<div
  bind:this={root}
  class={cn("fixed inset-0 z-40 flex flex-col overflow-y-auto bg-muted", {
    "text-[0.8125rem]": board.density !== "cozy",
    "text-[0.9375rem]": board.density === "cozy",
  })}
>
  <div
    class="sticky inset-bs-0 z-40 flex flex-wrap items-center gap-x-2 gap-y-1 border-be border-border bg-background px-3 py-1.5 text-[0.8125rem]"
  >
    {#if !isPresenting}
      <a
        href={resolve("/live/[...path]", { path: feed.election.slug })}
        class="inline-flex items-center gap-1 rounded-xs px-1.5 py-1 font-semibold hover:bg-muted"
      >
        <ArrowLeftIcon class="block-3.5 inline-3.5" aria-hidden="true" />
        {m.board_results()}
      </a>
    {/if}
    <h1 class="truncate font-semibold max-inline-[28ch]">
      {feed.election.title}
    </h1>
    <span
      class={cn("rounded-full px-2 py-0.5 text-[0.6875rem] font-semibold", {
        "bg-foreground text-background": status === "live",
        "border border-border text-muted-foreground": status === "before",
        "bg-(--no) text-background": status === "offline",
        "border border-dashed border-foreground": status === "rehearsal",
      })}
      title={status === "rehearsal" ? m.board_rehearsal_note() : undefined}
    >
      {#if status === "rehearsal"}
        {m.board_rehearsal()}
      {:else if status === "offline"}
        {m.night_status_offline()}
      {:else if status === "before"}
        {m.night_status_before()}
      {:else}
        {m.night_status_live()}
      {/if}
    </span>
    <nav
      class="order-last flex basis-full items-center gap-0.5 overflow-x-auto min-inline-0 lg:order-0 lg:flex-1 lg:basis-auto"
      aria-label={m.board_pages()}
    >
      {#each board.pages as entry, index (entry.id)}
        {const isCurrent = $derived(index === board.current)}
        <button
          type="button"
          class={cn(
            "shrink-0 rounded-xs px-2 py-1 whitespace-nowrap transition-colors",
            {
              "bg-foreground font-semibold text-background": isCurrent,
              "text-muted-foreground hover:bg-muted hover:text-foreground":
                !isCurrent,
            },
          )}
          aria-current={isCurrent ? "page" : undefined}
          onclick={() => go(index)}
          ondblclick={() => {
            if (!isFrozen) {
              renaming = { index, draft: entry.name };
            }
          }}
        >
          {#if index < 9}<span
              class="me-1 font-figures text-[0.85em] tabular-nums opacity-60"
              aria-hidden="true">{index + 1}</span
            >{/if}{entry.name}
        </button>
      {/each}
      {#if !isFrozen}
        <DropdownMenu.Root>
          <DropdownMenu.Trigger
            class="grid shrink-0 place-content-center rounded-xs text-muted-foreground block-7 inline-7 hover:bg-muted hover:text-foreground"
            aria-label={m.board_pages()}
          >
            <EllipsisIcon class="block-4 inline-4" aria-hidden="true" />
          </DropdownMenu.Trigger>
          <DropdownMenu.Content align="start" class="inline-52">
            <DropdownMenu.Item onSelect={addPage}>
              {m.board_page_add()}
            </DropdownMenu.Item>
            <DropdownMenu.Item
              onSelect={() =>
                (renaming = {
                  index: board.current,
                  draft: current?.name ?? "",
                })}
            >
              {m.board_page_rename()}
            </DropdownMenu.Item>
            <DropdownMenu.Item onSelect={() => duplicatePage(board.current)}>
              {m.board_page_duplicate()}
            </DropdownMenu.Item>
            <DropdownMenu.Item
              disabled={board.current === 0}
              onSelect={() => movePage(board.current, -1)}
            >
              {m.board_page_left()}
            </DropdownMenu.Item>
            <DropdownMenu.Item
              disabled={board.current === board.pages.length - 1}
              onSelect={() => movePage(board.current, 1)}
            >
              {m.board_page_right()}
            </DropdownMenu.Item>
            <DropdownMenu.Separator />
            <DropdownMenu.Item
              variant="destructive"
              disabled={board.pages.length === 1}
              onSelect={async () => removePage(board.current)}
            >
              {m.board_page_remove()}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Root>
      {/if}
    </nav>
    <div class="ms-auto flex items-center gap-1">
      {#if !isFrozen && !isStacked}
        <Button size="sm" variant="outline" onclick={() => (isAdding = true)}>
          <PlusIcon />{m.board_add()}
        </Button>
      {/if}
      {#if !isPresenting}
        <Button
          size="icon"
          variant="ghost"
          aria-label={isLocked ? m.board_unlock() : m.board_lock()}
          aria-pressed={isLocked}
          title={isLocked ? m.board_unlock() : m.board_lock()}
          onclick={() => (isLocked = !isLocked)}
        >
          {#if isLocked}<LockIcon />{:else}<LockOpenIcon />{/if}
        </Button>
        <Button
          size="icon"
          variant="ghost"
          aria-label={isShared ? m.board_shared() : m.board_share()}
          title={isShared ? m.board_shared() : m.board_share()}
          onclick={share}
        >
          <LinkIcon />
        </Button>
      {/if}
      <Button
        size="sm"
        variant={isPresenting ? "outline" : "default"}
        onclick={present}
      >
        {#if isPresenting}<MinimizeIcon
          />{m.board_present_exit()}{:else}<MaximizeIcon
          />{m.board_present()}{/if}
      </Button>
      {#if !isPresenting}
        <DropdownMenu.Root>
          <DropdownMenu.Trigger
            class="grid place-content-center rounded-sm text-muted-foreground block-8 inline-8 hover:bg-muted hover:text-foreground"
            aria-label={m.board_settings()}
          >
            <SettingsIcon class="block-4 inline-4" aria-hidden="true" />
          </DropdownMenu.Trigger>
          <DropdownMenu.Content align="end" class="inline-60">
            <DropdownMenu.Label>{m.board_density()}</DropdownMenu.Label>
            <DropdownMenu.RadioGroup
              value={board.density ?? "compact"}
              onValueChange={(value) => {
                board.density = value === "cozy" ? "cozy" : "compact";
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
              value={String(board.cycle ?? 0)}
              onValueChange={(value) => {
                board.cycle = Number(value);
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
            <DropdownMenu.Item variant="destructive" onSelect={reset}>
              {m.board_reset()}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Root>
      {/if}
    </div>
  </div>
  {#if status === "rehearsal"}
    <p
      class="border-be border-dashed border-border bg-background px-3 py-1 text-[0.75rem] text-muted-foreground"
    >
      {m.board_rehearsal_note()}
    </p>
  {/if}
  {#if isStacked}
    <p class="px-3 pbs-2 text-[0.75rem] text-muted-foreground">
      {m.board_stacked()}
    </p>
  {/if}
  <main class="flex-1 p-1.5 sm:p-2">
    <div
      bind:clientWidth={width}
      class={cn({ relative: !isStacked, "grid gap-1.5": isStacked })}
      style:block-size={isStacked ? undefined : `${rows * (ROW + GAP)}px`}
    >
      {#if width > 0 && current != null}
        {#if preview != null && !isStacked}
          {const spot = $derived(layout.get(preview.id))}
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
        {/if}
        {#each isStacked ? stacked : current.panels as entry (entry.id)}
          <Frame
            {desk}
            panel={entry}
            cell={shown(entry)}
            {column}
            isLocked={isFrozen}
            {isStacked}
            isActive={preview?.id === entry.id}
            onPreview={(cell: Cell, mode: "move" | "resize") => {
              preview = { id: entry.id, cell, mode };
            }}
            onCommit={(cell: Cell) => commit(entry.id, cell)}
            onRemove={() => remove(entry.id)}
            onDuplicate={() => duplicate(entry.id)}
            onChange={(patch: Partial<Panel>) => Object.assign(entry, patch)}
          />
        {/each}
      {/if}
    </div>
  </main>
</div>

<Dialog.Root bind:open={isAdding}>
  <Dialog.Content class="max-inline-2xl">
    <Dialog.Header>
      <Dialog.Title>{m.board_add_title()}</Dialog.Title>
      <Dialog.Description>{m.board_add_hint()}</Dialog.Description>
    </Dialog.Header>
    <ul class="grid grid-cols-1 gap-1 sm:grid-cols-2">
      {#each kinds as kind (kind)}
        <li>
          <button
            type="button"
            class="grid gap-0.5 rounded-sm border border-border px-3 py-2 text-start block-full inline-full hover:bg-muted focus-visible:bg-muted"
            onclick={() => add(kind)}
          >
            <span class="text-sm font-semibold">{KIND_TEXT[kind].name()}</span>
            <span class="text-xs text-muted-foreground"
              >{KIND_TEXT[kind].about()}</span
            >
          </button>
        </li>
      {/each}
    </ul>
  </Dialog.Content>
</Dialog.Root>

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
      {#each [["1–9", m.board_shortcut_pages()], ["[ ]", m.board_shortcut_next()], ["P", m.board_shortcut_present()], ["L", m.board_shortcut_lock()], ["A", m.board_shortcut_add()], ["← → ↑ ↓", m.board_shortcut_move()], ["Shift ← → ↑ ↓", m.board_shortcut_resize()]] as [keys, text] (keys)}
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
