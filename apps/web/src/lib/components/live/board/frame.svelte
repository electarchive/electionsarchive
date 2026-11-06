<script lang="ts">
  import type { LiveOffice } from "@ea/analytics/live";
  import { resolve } from "$app/paths";
  import EllipsisIcon from "@lucide/svelte/icons/ellipsis";
  import GripVerticalIcon from "@lucide/svelte/icons/grip-vertical";
  import { Draggable } from "@neodrag/svelte";
  import { on } from "svelte/events";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import type { Cell, Panel } from "#lib/live/board/layout.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Command from "#lib/components/ui/command/index.js";
  import * as Dialog from "#lib/components/ui/dialog/index.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { KIND_TEXT, short } from "#lib/live/board/labels.js";
  import { COLUMNS, GAP, ROW, SIZES } from "#lib/live/board/layout.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Chance from "./chance.svelte";
  import Closings from "./closings.svelte";
  import Counties from "./counties.svelte";
  import CountyMap from "./county-map.svelte";
  import HouseHexes from "./house-hexes.svelte";
  import NationMap from "./nation-map.svelte";
  import Note from "./note.svelte";
  import Power from "./power.svelte";
  import RaceList from "./race-list.svelte";
  import Results from "./results.svelte";
  import Seats from "./seats.svelte";
  import Ticker from "./ticker.svelte";
  import Trend from "./trend.svelte";

  const {
    desk,
    panel,
    cell,
    column,
    isLocked,
    isStacked,
    isActive,
    onPreview,
    onCommit,
    onRemove,
    onDuplicate,
    onChange,
  }: {
    desk: Desk;
    panel: Panel;
    cell: Cell;
    column: number;
    isLocked: boolean;
    isStacked: boolean;
    isActive: boolean;
    onPreview: (cell: Cell, mode: "move" | "resize") => void;
    onCommit: (cell: Cell) => void;
    onRemove: () => void;
    onDuplicate: () => void;
    onChange: (patch: Partial<Panel>) => void;
  } = $props();

  const RACE_KINDS = new Set(["race", "map", "chance", "trend", "counties"]);
  const OFFICE_KINDS = new Set([
    "power",
    "nation",
    "closest",
    "flips",
    "seats",
  ]);
  const OFFICES: Array<{ id: LiveOffice; label: () => string }> = [
    { id: "senate", label: m.board_page_senate },
    { id: "governor", label: m.board_page_governor },
    { id: "house", label: m.board_page_house },
  ];
  const MODES = [
    { id: "lead", label: m.board_mode_lead },
    { id: "shift", label: m.board_mode_shift },
    { id: "counted", label: m.board_mode_counted },
  ] as const;

  let origin = $state({ x: 0, y: 0 });
  let isDropped = $state(false);
  let stretch = $state<{ width: number; height: number } | null>(null);
  let isPicking = $state(false);
  let isRenaming = $state(false);
  let draft = $state("");

  const left = $derived(cell.x * (column + GAP));
  const top = $derived(cell.y * (ROW + GAP));
  const width = $derived(cell.w * column + (cell.w - 1) * GAP);
  const height = $derived(cell.h * ROW + (cell.h - 1) * GAP);
  const needsRace = $derived(RACE_KINDS.has(panel.kind));
  const race = $derived(needsRace ? desk.race(panel.race) : null);
  const isFollowing = $derived(needsRace && panel.race == null);
  const office = $derived(panel.office ?? "senate");
  const kindLabel = $derived(KIND_TEXT[panel.kind].name());
  const heading = $derived(
    panel.title ??
      (race == null
        ? OFFICE_KINDS.has(panel.kind) && panel.kind !== "hexes"
          ? `${kindLabel}: ${OFFICES.find((entry) => entry.id === office)?.label() ?? ""}`
          : kindLabel
        : `${short(race)}: ${kindLabel}`),
  );
  const races = $derived(
    desk.feed.election.races.toSorted(
      (a, b) =>
        a.state.localeCompare(b.state) ||
        a.office.localeCompare(b.office) ||
        Number(a.district ?? 0) - Number(b.district ?? 0),
    ),
  );

  const drag = new Draggable({
    get disabled() {
      return isLocked || isStacked;
    },
    get position() {
      return origin;
    },
    threshold: 3,
    onDrag: ({ offset }) => onPreview(moved(offset), "move"),
    onDragEnd: ({ offset }) => {
      onCommit(moved(offset));
      isDropped = true;
      origin = { x: 0, y: 0 };
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          isDropped = false;
        }),
      );
    },
  });

  function moved(offset: { x: number; y: number }): Cell {
    return {
      x: Math.max(
        0,
        Math.min(
          COLUMNS - panel.w,
          Math.round((left + offset.x) / (column + GAP)),
        ),
      ),
      y: Math.max(0, Math.round((top + offset.y) / (ROW + GAP))),
      w: panel.w,
      h: panel.h,
    };
  }

  function sized(next: { width: number; height: number }): Cell {
    const size = SIZES[panel.kind];
    return {
      x: panel.x,
      y: panel.y,
      w: Math.max(
        size.minW,
        Math.min(
          COLUMNS - panel.x,
          Math.round((next.width + GAP) / (column + GAP)),
        ),
      ),
      h: Math.max(size.minH, Math.round((next.height + GAP) / (ROW + GAP))),
    };
  }

  function grip(event: PointerEvent) {
    if (isLocked || isStacked || event.button !== 0) {
      return;
    }
    event.preventDefault();
    const target = event.currentTarget;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    target.setPointerCapture(event.pointerId);
    const start = { x: event.clientX, y: event.clientY, width, height };
    const move = (next: PointerEvent) => {
      stretch = {
        width: Math.max(column, start.width + next.clientX - start.x),
        height: Math.max(ROW, start.height + next.clientY - start.y),
      };
      onPreview(sized(stretch), "resize");
    };
    const stops: Array<() => void> = [];
    const end = () => {
      if (stretch != null) {
        onCommit(sized(stretch));
      }
      stretch = null;
      for (const stop of stops) {
        stop();
      }
    };
    stops.push(
      on(target, "pointermove", move),
      on(target, "pointerup", end),
      on(target, "pointercancel", end),
    );
  }

  function nudge(event: KeyboardEvent) {
    const steps: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    };
    const step = steps[event.key];
    if (step == null || isLocked || isStacked) {
      return;
    }
    event.preventDefault();
    const [dx, dy] = step;
    const size = SIZES[panel.kind];
    onCommit(
      event.shiftKey
        ? {
            x: panel.x,
            y: panel.y,
            w: Math.max(size.minW, Math.min(COLUMNS - panel.x, panel.w + dx)),
            h: Math.max(size.minH, panel.h + dy),
          }
        : {
            x: Math.max(0, Math.min(COLUMNS - panel.w, panel.x + dx)),
            y: Math.max(0, panel.y + dy),
            w: panel.w,
            h: panel.h,
          },
    );
  }

  function rename() {
    const name = draft.trim().slice(0, 80);
    onChange({ title: name === "" ? undefined : name });
    isRenaming = false;
  }
</script>

<section
  {...drag.attach}
  class={cn(
    "group/frame flex flex-col overflow-hidden rounded-sm border border-border bg-background",
    { "absolute inset-s-0 inset-bs-0": !isStacked, relative: isStacked },
    {
      "transition-[transform,inline-size,block-size] duration-200 ease-out motion-reduce:transition-none":
        !drag.isDragging && !isDropped && stretch == null,
    },
    {
      "z-30 shadow-menu ring-1 ring-foreground/25":
        drag.isDragging || stretch != null,
      "z-10": isActive && !drag.isDragging && stretch == null,
    },
  )}
  style:transform={isStacked ? undefined : `translate(${left}px, ${top}px)`}
  style:inline-size={isStacked ? undefined : `${stretch?.width ?? width}px`}
  style:block-size="{stretch?.height ?? height}px"
  aria-label={heading}
  data-panel={panel.id}
>
  <header
    {...drag.handle()}
    class={cn(
      "flex shrink-0 items-center gap-1 border-be border-border/70 ps-1 pe-0.5 block-6",
      { "cursor-grab active:cursor-grabbing": !isLocked && !isStacked },
    )}
  >
    {#if !isLocked && !isStacked}
      <button
        type="button"
        class="grid shrink-0 place-content-center rounded-xs text-muted-foreground opacity-60 block-5 inline-4 group-hover/frame:opacity-100 hover:text-foreground focus-visible:opacity-100"
        aria-label={m.board_panel_move()}
        onkeydown={nudge}
      >
        <GripVerticalIcon class="block-3.5 inline-3.5" aria-hidden="true" />
      </button>
    {/if}
    <h3 class="flex-1 truncate text-[0.75rem] font-semibold min-inline-0">
      {heading}
    </h3>
    {#if isFollowing}
      <span
        class="shrink-0 rounded-full bg-foreground/70 block-1.5 inline-1.5"
        title={m.board_following()}
      ></span>
      <span class="sr-only">{m.board_following()}</span>
    {/if}
    <span {...drag.cancel()} class="contents">
      <DropdownMenu.Root>
        <DropdownMenu.Trigger
          class="grid shrink-0 place-content-center rounded-xs text-muted-foreground block-5 inline-5 hover:bg-muted hover:text-foreground"
          aria-label={m.board_panel_menu()}
        >
          <EllipsisIcon class="block-3.5 inline-3.5" aria-hidden="true" />
        </DropdownMenu.Trigger>
        <DropdownMenu.Content align="end" class="inline-60">
          {#if needsRace}
            <DropdownMenu.Item onSelect={() => (isPicking = true)}>
              {m.board_panel_race()}
            </DropdownMenu.Item>
            <DropdownMenu.CheckboxItem
              checked={panel.race == null}
              onCheckedChange={(on: boolean) => {
                onChange({ race: on ? null : (desk.selected?.slug ?? null) });
              }}
            >
              {m.board_panel_follow()}
            </DropdownMenu.CheckboxItem>
            <DropdownMenu.Separator />
          {/if}
          {#if OFFICE_KINDS.has(panel.kind)}
            <DropdownMenu.Label>{m.board_panel_office()}</DropdownMenu.Label>
            <DropdownMenu.RadioGroup
              value={office}
              onValueChange={(value) => {
                const found = OFFICES.find((entry) => entry.id === value);
                if (found != null) {
                  onChange({ office: found.id });
                }
              }}
            >
              {#each OFFICES as entry (entry.id)}
                <DropdownMenu.RadioItem value={entry.id}
                  >{entry.label()}</DropdownMenu.RadioItem
                >
              {/each}
            </DropdownMenu.RadioGroup>
            <DropdownMenu.Separator />
          {/if}
          {#if panel.kind === "map"}
            <DropdownMenu.Label>{m.board_panel_mode()}</DropdownMenu.Label>
            <DropdownMenu.RadioGroup
              value={panel.mode ?? "lead"}
              onValueChange={(value) => {
                const found = MODES.find((entry) => entry.id === value);
                if (found != null) {
                  onChange({ mode: found.id });
                }
              }}
            >
              {#each MODES as entry (entry.id)}
                <DropdownMenu.RadioItem value={entry.id}
                  >{entry.label()}</DropdownMenu.RadioItem
                >
              {/each}
            </DropdownMenu.RadioGroup>
            <DropdownMenu.Separator />
          {/if}
          <DropdownMenu.Item
            onSelect={() => {
              draft = panel.title ?? heading;
              isRenaming = true;
            }}
          >
            {m.board_panel_rename()}
          </DropdownMenu.Item>
          {#if race != null}
            <DropdownMenu.Item>
              {#snippet child({ props })}
                <a
                  {...props}
                  href={resolve("/live/[...path]", { path: race.slug })}
                  >{m.board_panel_open()}</a
                >
              {/snippet}
            </DropdownMenu.Item>
          {/if}
          {#if !isLocked}
            <DropdownMenu.Item onSelect={onDuplicate}>
              {m.board_panel_duplicate()}
            </DropdownMenu.Item>
            <DropdownMenu.Item variant="destructive" onSelect={onRemove}>
              {m.board_panel_remove()}
            </DropdownMenu.Item>
          {/if}
        </DropdownMenu.Content>
      </DropdownMenu.Root>
    </span>
  </header>
  <div
    class="@container flex-1 overflow-auto overscroll-contain p-1.5 min-block-0"
  >
    {#if needsRace && race == null}
      <p class="text-[0.75em] text-muted-foreground">{m.board_follow_hint()}</p>
    {:else if race != null && panel.kind === "race"}
      <Results {desk} {race} />
    {:else if race != null && panel.kind === "map"}
      <CountyMap {desk} {race} mode={panel.mode ?? "lead"} />
    {:else if race != null && panel.kind === "chance"}
      <Chance {desk} {race} />
    {:else if race != null && panel.kind === "trend"}
      <Trend {desk} {race} />
    {:else if race != null && panel.kind === "counties"}
      <Counties {desk} {race} />
    {:else if panel.kind === "nation"}
      <NationMap {desk} {office} />
    {:else if panel.kind === "hexes"}
      <HouseHexes {desk} />
    {:else if panel.kind === "power"}
      <Power {desk} {office} />
    {:else if panel.kind === "closest" || panel.kind === "flips"}
      <RaceList {desk} kind={panel.kind} {office} />
    {:else if panel.kind === "seats"}
      <Seats {desk} {office} />
    {:else if panel.kind === "ticker"}
      <Ticker {desk} />
    {:else if panel.kind === "closings"}
      <Closings {desk} />
    {:else if panel.kind === "note"}
      <Note text={panel.text} onInput={(text) => onChange({ text })} />
    {/if}
  </div>
  {#if !isLocked && !isStacked}
    <span
      class="absolute inset-e-0 inset-be-0 z-10 cursor-se-resize touch-none opacity-0 block-3.5 inline-3.5 group-hover/frame:opacity-100"
      role="presentation"
      aria-label={m.board_panel_resize()}
      onpointerdown={grip}
    >
      <svg
        viewBox="0 0 10 10"
        class="text-muted-foreground block-full inline-full"
      >
        <path
          d="M9 3 3 9M9 6 6 9"
          stroke="currentColor"
          stroke-width="1.25"
          stroke-linecap="round"
        />
      </svg>
    </span>
  {/if}
</section>

<Command.Dialog
  bind:open={isPicking}
  title={m.board_panel_race()}
  description={m.board_race_search()}
>
  <Command.Input placeholder={m.board_race_search()} />
  <Command.List>
    <Command.Empty>{m.board_race_empty()}</Command.Empty>
    {#each races as entry (entry.id)}
      <Command.Item
        value={`${short(entry)} ${entry.title}`}
        onSelect={() => {
          onChange({ race: entry.slug });
          isPicking = false;
        }}
      >
        <span class="font-semibold">{short(entry)}</span>
        <span class="truncate text-muted-foreground">{entry.title}</span>
      </Command.Item>
    {/each}
  </Command.List>
</Command.Dialog>

<Dialog.Root bind:open={isRenaming}>
  <Dialog.Content class="max-inline-sm">
    <Dialog.Header>
      <Dialog.Title>{m.board_panel_rename()}</Dialog.Title>
    </Dialog.Header>
    <form
      class="grid gap-3"
      onsubmit={(event) => {
        event.preventDefault();
        rename();
      }}
    >
      <Input
        bind:value={draft}
        aria-label={m.board_panel_title()}
        maxlength={80}
      />
      <Dialog.Footer>
        <Button type="submit">{m.board_panel_rename()}</Button>
      </Dialog.Footer>
    </form>
  </Dialog.Content>
</Dialog.Root>
