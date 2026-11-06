<script lang="ts">
  import { resolve } from "$app/paths";
  import { encodeId } from "@ea/view-spec";
  import EllipsisIcon from "@lucide/svelte/icons/ellipsis";
  import GripVerticalIcon from "@lucide/svelte/icons/grip-vertical";
  import { Draggable } from "@neodrag/svelte";
  import { createQuery, keepPreviousData } from "@tanstack/svelte-query";
  import { Pending } from "svelte-islands";
  import { toast } from "svelte-sonner";
  import { on } from "svelte/events";
  import Note from "#lib/components/live/board/note.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Dialog from "#lib/components/ui/dialog/index.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import type { Desk } from "./desk.svelte.js";
  import type { Cell, Spec } from "./grid.js";
  import { located, span } from "./desk.svelte.js";
  import { download, files } from "./export.js";
  import { COLUMNS, GAP, KIND_TEXT, kindOf, ROW, SIZES } from "./grid.js";
  import Hemicycle from "./hemicycle.svelte";
  import List from "./list.svelte";
  import PlaceMap from "./map.svelte";
  import Picker from "./picker.svelte";
  import Place from "./place.svelte";
  import Places from "./places.svelte";
  import Results from "./results.svelte";
  import Scatter from "./scatter.svelte";
  import Strip from "./strip.svelte";
  import Summary from "./summary.svelte";

  const {
    id,
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
    id: string;
    desk: Desk;
    panel: Spec;
    cell: Cell;
    column: number;
    isLocked: boolean;
    isStacked: boolean;
    isActive: boolean;
    onPreview: (cell: Cell, mode: "move" | "resize") => void;
    onCommit: (cell: Cell) => void;
    onRemove: () => void;
    onDuplicate: () => void;
    onChange: (patch: {
      contest?: string;
      title?: string;
      text?: string;
    }) => void;
  } = $props();

  let origin = $state({ x: 0, y: 0 });
  let isDropped = $state(false);
  let stretch = $state<{ width: number; height: number } | null>(null);
  let isPicking = $state(false);
  let isRenaming = $state(false);
  let draft = $state("");

  const kind = $derived(kindOf(panel));
  const isTall = $derived(
    kind === "map" || kind === "scatter" || kind === "strip",
  );
  const area = $derived(panel.area ?? cell);
  const left = $derived(cell.x * (column + GAP));
  const top = $derived(cell.y * (ROW + GAP));
  const width = $derived(cell.w * column + (cell.w - 1) * GAP);
  const height = $derived(cell.h * ROW + (cell.h - 1) * GAP);
  const isOwn = $derived(panel.contest != null && panel.contest !== desk.main);
  const input = $derived(desk.input(panel.contest));
  const query = createQuery(() => ({
    ...orpc.contests.get.queryOptions({ input }),
    enabled: kind !== "note" && input.id !== "",
    placeholderData: keepPreviousData,
  }));
  const contest = $derived(query.data ?? null);
  const focus = $derived(contest == null ? null : desk.focused(contest));
  const kindLabel = $derived(KIND_TEXT[kind].name());
  const heading = $derived(
    panel.title ??
      (isOwn && contest != null
        ? `${kindLabel}: ${contest.scope.name} ${contest.election.year ?? contest.election.name}`
        : kindLabel),
  );
  const quick = $derived(
    (contest?.targets ?? []).map((target) => ({
      id: target.value,
      label: target.label,
      detail: target.detail == null ? null : date(target.detail),
    })),
  );
  const exports = $derived(
    contest == null || kind === "note"
      ? []
      : files(kind, contest, focus, $state.snapshot(desk.spec)),
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
          COLUMNS - area.w,
          Math.round((left + offset.x) / (column + GAP)),
        ),
      ),
      y: Math.max(0, Math.round((top + offset.y) / (ROW + GAP))),
      w: area.w,
      h: area.h,
    };
  }

  function sized(next: { width: number; height: number }): Cell {
    const size = SIZES[kind];
    return {
      x: area.x,
      y: area.y,
      w: Math.max(
        size.minW,
        Math.min(
          COLUMNS - area.x,
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
    const size = SIZES[kind];
    onCommit(
      event.shiftKey
        ? {
            x: area.x,
            y: area.y,
            w: Math.max(size.minW, Math.min(COLUMNS - area.x, area.w + dx)),
            h: Math.max(size.minH, area.h + dy),
          }
        : {
            x: Math.max(0, Math.min(COLUMNS - area.w, area.x + dx)),
            y: Math.max(0, area.y + dy),
            w: area.w,
            h: area.h,
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
  style:block-size={isStacked
    ? isTall
      ? `${Math.max(height, 320)}px`
      : undefined
    : `${stretch?.height ?? height}px`}
  style:max-block-size={isStacked ? "80dvh" : undefined}
  aria-label={heading}
  data-panel={id}
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
    {:else}
      <span class="inline-1" aria-hidden="true"></span>
    {/if}
    <h3 class="flex-1 truncate text-[0.75rem] font-semibold min-inline-0">
      {heading}
    </h3>
    {#if query.isFetching && contest != null}
      <span
        class="shrink-0 animate-pulse rounded-full bg-foreground/50 block-1.5 inline-1.5 motion-reduce:animate-none"
        aria-hidden="true"
      ></span>
    {/if}
    <span {...drag.cancel()} class="contents">
      <DropdownMenu.Root>
        <DropdownMenu.Trigger
          class="grid shrink-0 place-content-center rounded-xs text-muted-foreground block-5 inline-5 hover:bg-muted hover:text-foreground"
          aria-label={m.board_panel_menu()}
        >
          <EllipsisIcon class="block-3.5 inline-3.5" aria-hidden="true" />
        </DropdownMenu.Trigger>
        <DropdownMenu.Content align="end" class="inline-64">
          {#if kind !== "note"}
            <DropdownMenu.Item onSelect={() => (isPicking = true)}>
              {m.workbench_panel_contest()}
            </DropdownMenu.Item>
            <DropdownMenu.CheckboxItem
              checked={!isOwn}
              disabled={!isOwn}
              onCheckedChange={(next: boolean) => {
                if (next) {
                  onChange({ contest: undefined });
                }
              }}
            >
              {m.workbench_panel_follow()}
            </DropdownMenu.CheckboxItem>
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
          {#if contest?.path != null}
            {const path = $derived(contest.path)}
            <DropdownMenu.Item>
              {#snippet child({ props })}
                <a {...props} href={resolve("/[...path]", { path })}
                  >{m.workbench_open()}</a
                >
              {/snippet}
            </DropdownMenu.Item>
          {/if}
          {#if exports.length > 0}
            <DropdownMenu.Sub>
              <DropdownMenu.SubTrigger
                >{m.workbench_export()}</DropdownMenu.SubTrigger
              >
              <DropdownMenu.SubContent class="inline-48">
                {#each exports as file (file.name)}
                  <DropdownMenu.Item onSelect={() => download(file)}
                    >{file.label}</DropdownMenu.Item
                  >
                {/each}
              </DropdownMenu.SubContent>
            </DropdownMenu.Sub>
          {/if}
          {#if !isLocked}
            <DropdownMenu.Separator />
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
    {#if panel.kind === "note"}
      <Note text={panel.text} onInput={(text: string) => onChange({ text })} />
    {:else if contest == null}
      {#if query.isError}
        <p class="text-[0.75em] text-muted-foreground">
          {m.workbench_panel_error()}
        </p>
      {:else}
        <Pending
          name="workbench.panel"
          isPending={true}
          class="[--island-inset:0]"
        />
      {/if}
    {:else if kind === "summary"}
      <Summary {contest} />
    {:else if kind === "bar"}
      <Results
        {contest}
        focus={focus?.key ?? null}
        picked={desk.picked}
        onfocus={(key: string) => {
          desk.focus = key;
        }}
      />
    {:else if contest.places == null || contest.places.rows.length === 0}
      {#if kind === "hemicycle"}
        <Hemicycle {contest} span={span(contest)} />
      {:else}
        <p class="text-[0.75em] text-muted-foreground">
          {m.workbench_no_places()}
        </p>
      {/if}
    {:else if kind === "place"}
      <Place {contest} {desk} />
    {:else if kind === "map"}
      <PlaceMap id={input.id} {contest} {desk} span={span(contest)} />
    {:else if kind === "table"}
      <Places
        {contest}
        {focus}
        picked={desk.picked}
        bind:hovered={desk.hovered}
      />
    {:else if kind === "closest" || kind === "movers" || kind === "flips"}
      <List {contest} {desk} {focus} {kind} />
    {:else if kind === "scatter"}
      <Scatter
        {contest}
        {focus}
        picked={desk.picked}
        bind:hovered={desk.hovered}
      />
    {:else if kind === "strip"}
      <Strip {contest} {desk} />
    {:else if kind === "hemicycle"}
      <Hemicycle {contest} span={span(contest)} />
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

{#if kind !== "note"}
  <Picker
    bind:open={isPicking}
    title={m.workbench_panel_contest()}
    {quick}
    quickLabel={m.workbench_panel_earlier()}
    onPick={(value: string) => {
      void located(value).then(
        (id) => onChange({ contest: encodeId("ct", id) }),
        () => toast.error(m.workbench_panel_error()),
      );
    }}
  />
{/if}

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
