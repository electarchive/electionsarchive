<script lang="ts">
  import ChevronLeft from "@lucide/svelte/icons/chevron-left";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import ZoomIn from "@lucide/svelte/icons/zoom-in";
  import ZoomOut from "@lucide/svelte/icons/zoom-out";
  import { tick } from "svelte";
  import type { Region, Upload } from "#lib/sheet.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  let {
    upload,
    page = $bindable(),
    marks,
    selected,
    armed,
    locked,
    onmark,
    onpick,
    onunmark,
  }: {
    upload: Upload;
    page: number;
    marks: Array<{ key: string; page: number; region: Region; label: string }>;
    selected: string | null;
    armed: string | null;
    locked: boolean;
    onmark: (region: Region) => void;
    onpick: (key: string) => void;
    onunmark: () => void;
  } = $props();

  const ZOOMS = [0.5, 0.75, 1, 1.5, 2, 3];

  let zoom = $state(1);
  let frame = $state(0);
  let draft = $state<Region | null>(null);
  let start: [number, number] | null = null;
  let canvas = $state<HTMLDivElement | null>(null);

  const pages = $derived(upload.pages ?? 1);
  const shown = $derived(marks.filter((mark) => mark.page === page));
  const active = $derived(marks.find((mark) => mark.key === selected) ?? null);
  const width = $derived(
    frame === 0 ? null : Math.max(160, Math.floor((frame - 24) * zoom)),
  );

  $effect(() => {
    const key = active?.key;
    if (key == null || active?.page !== page) {
      return;
    }
    void tick().then(() =>
      canvas
        ?.querySelector(`[data-mark="${CSS.escape(key)}"]`)
        ?.scrollIntoView({ block: "nearest", inline: "nearest" }),
    );
  });

  const turn = (step: number) => {
    page = Math.min(pages, Math.max(1, page + step));
  };

  const scale = (step: number) => {
    const at = ZOOMS.findIndex((level) => level >= zoom);
    zoom = ZOOMS[Math.min(ZOOMS.length - 1, Math.max(0, at + step))] ?? 1;
  };

  const at = (event: PointerEvent): [number, number] => {
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    return [
      Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)),
      Math.min(1, Math.max(0, (event.clientY - box.top) / box.height)),
    ];
  };

  const span = (a: [number, number], b: [number, number]): Region => [
    Math.min(a[0], b[0]),
    Math.min(a[1], b[1]),
    Math.max(a[0], b[0]),
    Math.max(a[1], b[1]),
  ];

  const down = (event: PointerEvent) => {
    if (event.button !== 0) {
      return;
    }
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    start = at(event);
    draft = null;
  };

  const move = (event: PointerEvent) => {
    if (start != null && armed != null && !locked) {
      draft = span(start, at(event));
    }
  };

  const up = (event: PointerEvent) => {
    if (start == null) {
      return;
    }
    const point = at(event);
    const region = span(start, point);
    start = null;
    draft = null;
    if (
      armed != null &&
      !locked &&
      region[2] - region[0] > 0.004 &&
      region[3] - region[1] > 0.002
    ) {
      onmark(region.map((part) => Math.round(part * 1e5) / 1e5) as Region);
      return;
    }
    const hit = shown.findLast(
      (mark) =>
        point[0] >= mark.region[0] &&
        point[0] <= mark.region[2] &&
        point[1] >= mark.region[1] &&
        point[1] <= mark.region[3],
    );
    if (hit != null) {
      onpick(hit.key);
    }
  };
</script>

<div class="grid grid-rows-[auto_auto_minmax(0,1fr)] block-full min-block-0">
  <div class="flex flex-wrap items-center gap-1  border-be px-2 py-1">
    <Button
      variant="ghost"
      size="icon"
      class="block-7 inline-7"
      aria-label={m.upload_previous()}
      disabled={page <= 1}
      onclick={() => turn(-1)}><ChevronLeft /></Button
    >
    <span
      class="font-figures text-xs whitespace-nowrap tabular-nums"
      aria-live="polite">{m.upload_page({ page, pages: count(pages) })}</span
    >
    <Button
      variant="ghost"
      size="icon"
      class="block-7 inline-7"
      aria-label={m.upload_next()}
      disabled={page >= pages}
      onclick={() => turn(1)}><ChevronRight /></Button
    >
    <span class="mx-1 bg-border block-4 inline-px" aria-hidden="true"></span>
    <Button
      variant="ghost"
      size="icon"
      class="block-7 inline-7"
      aria-label={m.upload_zoom_out()}
      disabled={zoom <= (ZOOMS[0] ?? 0)}
      onclick={() => scale(-1)}><ZoomOut /></Button
    >
    <button
      type="button"
      class="rounded-sm px-1 font-figures text-xs tabular-nums min-inline-11 hover:bg-muted"
      aria-label={m.ws_scan_fit()}
      title={m.ws_scan_fit()}
      onclick={() => (zoom = 1)}>{percent(zoom, 0)}</button
    >
    <Button
      variant="ghost"
      size="icon"
      class="block-7 inline-7"
      aria-label={m.upload_zoom_in()}
      disabled={zoom >= (ZOOMS.at(-1) ?? 3)}
      onclick={() => scale(1)}><ZoomIn /></Button
    >
    <span
      class="ms-auto truncate text-xs text-muted-foreground max-inline-48 min-inline-0"
      title={upload.filename}>{upload.filename}</span
    >
  </div>
  <p
    class={cn(
      "flex items-baseline justify-between gap-3 border-be px-3 py-1.5 text-xs text-pretty",
      {
        "bg-marker/30": armed != null && !locked,
        "text-muted-foreground": armed == null || locked,
      },
    )}
  >
    <span
      >{armed == null || locked
        ? m.ws_scan_hint_idle()
        : m.ws_scan_hint_armed({ cell: armed })}</span
    >
    {#if active != null && !locked}
      <button
        type="button"
        class="shrink-0 underline underline-offset-2 hover:text-foreground"
        onclick={onunmark}>{m.ws_scan_remove_box()}</button
      >
    {/if}
  </p>
  <div
    class="overflow-auto overscroll-contain bg-muted p-3 min-block-0"
    bind:clientWidth={frame}
  >
    <div
      bind:this={canvas}
      class={cn("relative mx-auto touch-none bg-white shadow-xs select-none", {
        "cursor-crosshair": armed != null && !locked,
      })}
      style:inline-size={width == null ? "100%" : `${width}px`}
      onpointerdown={down}
      onpointermove={move}
      onpointerup={up}
      onpointercancel={() => {
        start = null;
        draft = null;
      }}
      role="presentation"
    >
      <img
        class="block inline-full"
        src="/uploads/{upload.id}/pages/{page}"
        alt={m.upload_page_alt({ page, filename: upload.filename })}
        draggable="false"
      />
      {#each shown as mark (mark.key)}
        {const isActive = $derived(mark.key === selected)}
        <div
          data-mark={mark.key}
          class={cn(
            "pointer-events-none absolute rounded-xs mix-blend-multiply",
            {
              "bg-marker/70 ring-[1.5px] ring-ring": isActive,
              "bg-marker/25 ring-1 ring-foreground/35": !isActive,
            },
          )}
          style:inset-inline-start="{mark.region[0] * 100}%"
          style:inset-block-start="{mark.region[1] * 100}%"
          style:inline-size="{(mark.region[2] - mark.region[0]) * 100}%"
          style:block-size="{(mark.region[3] - mark.region[1]) * 100}%"
          title={mark.label}
        ></div>
        {#if isActive}
          <span
            class="pointer-events-none absolute -translate-y-full rounded-t-xs bg-foreground px-1 text-[11px]/4  whitespace-nowrap text-background"
            style:inset-inline-start="{mark.region[0] * 100}%"
            style:inset-block-start="{mark.region[1] * 100}%">{mark.label}</span
          >
        {/if}
      {/each}
      {#if draft != null}
        <div
          class="pointer-events-none absolute rounded-xs bg-marker/40 mix-blend-multiply outline-[1.5px] outline-ring outline-dashed"
          style:inset-inline-start="{draft[0] * 100}%"
          style:inset-block-start="{draft[1] * 100}%"
          style:inline-size="{(draft[2] - draft[0]) * 100}%"
          style:block-size="{(draft[3] - draft[1]) * 100}%"
        ></div>
      {/if}
    </div>
  </div>
</div>
