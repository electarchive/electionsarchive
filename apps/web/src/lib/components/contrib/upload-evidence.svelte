<script lang="ts">
  import ChevronLeft from "@lucide/svelte/icons/chevron-left";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import ZoomIn from "@lucide/svelte/icons/zoom-in";
  import ZoomOut from "@lucide/svelte/icons/zoom-out";
  import { onDestroy } from "svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Checkbox } from "#lib/components/ui/checkbox/index.js";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  type Region = [number, number, number, number];

  let {
    value = $bindable(),
  }: {
    value: { id: string; page: number | null; region: Region | null } | null;
  } = $props();

  const LIMIT = 200 * 1024 ** 2;

  let phase = $state<"idle" | "sending" | "reading" | "ready" | "failed">(
    "idle",
  );
  let sent = $state(0);
  let problem = $state<string | null>(null);
  let filename = $state("");
  let pages = $state<number | null>(null);
  let shared = $state(false);
  let over = $state(false);
  let zoomed = $state(false);
  let draft = $state<Region | null>(null);
  let start: [number, number] | null = null;
  let request: XMLHttpRequest | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const stop = () => {
    request?.abort();
    request = null;
    clearTimeout(timer);
  };

  onDestroy(stop);

  const reset = () => {
    stop();
    value = null;
    phase = "idle";
    problem = null;
    pages = null;
    zoomed = false;
    draft = null;
  };

  const check = async (id: string) => {
    try {
      const response = await fetch(`/uploads/${id}`);
      const body = (await response.json()) as {
        status?: "received" | "ready" | "rejected";
        pages?: number | null;
        error?: string | null;
        message?: string;
      };
      if (body.status === "ready") {
        pages = body.pages ?? null;
        value = { id, page: body.pages == null ? null : 1, region: null };
        phase = "ready";
      } else if (body.status === "rejected" || !response.ok) {
        problem = m.upload_rejected({
          reason: body.error ?? body.message ?? response.statusText,
        });
        phase = "failed";
      } else {
        timer = setTimeout(() => void check(id), 1200);
      }
    } catch (caught) {
      problem = m.upload_failed({ reason: String(caught) });
      phase = "failed";
    }
  };

  const send = (file: File) => {
    reset();
    filename = file.name;
    if (file.size > LIMIT) {
      problem = m.upload_too_large();
      phase = "failed";
      return;
    }
    phase = "sending";
    sent = 0;
    const xhr = new XMLHttpRequest();
    request = xhr;
    xhr.open(
      "POST",
      `/uploads?filename=${encodeURIComponent(file.name)}&public_record=${shared}`,
    );
    xhr.setRequestHeader("content-type", "application/octet-stream");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        sent = event.loaded / event.total;
      }
    };
    xhr.onload = () => {
      const body = JSON.parse(xhr.responseText || "{}") as {
        id?: string;
        status?: string;
        pages?: number | null;
        error?: string | null;
        message?: string;
      };
      if (xhr.status >= 400 || body.id == null) {
        problem = m.upload_failed({ reason: body.message ?? xhr.statusText });
        phase = "failed";
        return;
      }
      phase = "reading";
      const id = body.id;
      timer = setTimeout(() => void check(id), 1200);
    };
    xhr.onerror = () => {
      problem = m.upload_failed({ reason: xhr.statusText || "network" });
      phase = "failed";
    };
    xhr.send(file);
  };

  const chosen = (event: Event & { currentTarget: HTMLInputElement }) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (file != null) {
      send(file);
    }
  };

  const dropped = (event: DragEvent) => {
    event.preventDefault();
    over = false;
    const file = event.dataTransfer?.files[0];
    if (file != null) {
      send(file);
    }
  };

  const turn = (step: number) => {
    if (value?.page == null || pages == null) {
      return;
    }
    const next = Math.min(pages, Math.max(1, value.page + step));
    if (next !== value.page) {
      value = { ...value, page: next, region: null };
    }
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
    if (start != null) {
      draft = span(start, at(event));
    }
  };

  const up = (event: PointerEvent) => {
    if (start == null || value == null) {
      return;
    }
    const region = span(start, at(event));
    start = null;
    draft = null;
    if (region[2] - region[0] > 0.004 && region[3] - region[1] > 0.002) {
      value = {
        ...value,
        region: region.map((part) => Math.round(part * 1e5) / 1e5) as Region,
      };
    }
  };

  const shown = $derived(draft ?? value?.region ?? null);
</script>

{#if phase === "ready" && value != null}
  <div class="contrib-settle grid gap-2">
    <p class="flex items-baseline justify-between gap-3 text-sm">
      <span class="truncate font-semibold" title={filename}>{filename}</span>
      <button
        type="button"
        class="shrink-0 text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
        onclick={reset}>{m.upload_replace()}</button
      >
    </p>
    {#if value.page == null || pages == null}
      <p class="text-xs text-muted-foreground">{m.upload_no_pages()}</p>
    {:else}
      <div
        class="overflow-auto overscroll-contain rounded-sm bg-white ring-1 ring-border max-block-[min(60dvh,32rem)]"
      >
        <div
          class="relative cursor-crosshair touch-none select-none"
          style:inline-size={zoomed ? "200%" : "100%"}
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
            src="/uploads/{value.id}/pages/{value.page}"
            alt={m.upload_page_alt({ page: value.page, filename })}
            draggable="false"
          />
          {#if shown != null}
            <div
              class={cn(
                "pointer-events-none absolute rounded-[2px] bg-marker/55 mix-blend-multiply ring-[1.5px] ring-ring",
                { "bg-marker/35": draft != null },
              )}
              style:inset-inline-start="{shown[0] * 100}%"
              style:inset-block-start="{shown[1] * 100}%"
              style:inline-size="{(shown[2] - shown[0]) * 100}%"
              style:block-size="{(shown[3] - shown[1]) * 100}%"
            ></div>
          {/if}
        </div>
      </div>
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            class="block-8 inline-8"
            aria-label={m.upload_previous()}
            disabled={value.page <= 1}
            onclick={() => turn(-1)}><ChevronLeft /></Button
          >
          <span class="font-figures text-sm tabular-nums" aria-live="polite"
            >{m.upload_page({ page: value.page, pages: count(pages) })}</span
          >
          <Button
            variant="ghost"
            size="icon"
            class="block-8 inline-8"
            aria-label={m.upload_next()}
            disabled={value.page >= pages}
            onclick={() => turn(1)}><ChevronRight /></Button
          >
        </div>
        <Button
          variant="ghost"
          size="icon"
          class="block-8 inline-8"
          aria-label={zoomed ? m.upload_zoom_out() : m.upload_zoom_in()}
          aria-pressed={zoomed}
          onclick={() => (zoomed = !zoomed)}
          >{#if zoomed}<ZoomOut />{:else}<ZoomIn />{/if}</Button
        >
      </div>
      <p
        class="flex items-baseline justify-between gap-3 text-xs text-pretty text-muted-foreground"
      >
        <span>{m.upload_mark_hint()}</span>
        {#if value.region != null}
          <button
            type="button"
            class="shrink-0 underline underline-offset-2 hover:text-foreground"
            onclick={() => {
              if (value != null) {
                value = { ...value, region: null };
              }
            }}>{m.upload_mark_clear()}</button
          >
        {/if}
      </p>
    {/if}
  </div>
{:else if phase === "sending" || phase === "reading"}
  <div class="grid gap-1.5 py-1" role="status">
    <p class="flex items-baseline justify-between gap-3 text-sm">
      <span class="truncate">{filename}</span>
      <span class="shrink-0 font-figures text-muted-foreground tabular-nums"
        >{phase === "sending"
          ? m.upload_sending({ percent: percent(sent, 0) })
          : m.upload_reading()}</span
      >
    </p>
    <div class="overflow-hidden bg-border block-0.5">
      <div
        class={cn("bg-foreground block-full", {
          "upload-reading": phase === "reading",
        })}
        style:inline-size="{phase === 'sending' ? sent * 100 : 100}%"
      ></div>
    </div>
  </div>
{:else}
  <div class="grid gap-2">
    <label
      class={cn(
        "grid cursor-pointer place-items-center gap-0.5 rounded-sm border border-dashed border-input px-3 py-5 text-center focus-within:border-foreground focus-within:ring-[1.5px] focus-within:ring-ring hover:border-foreground",
        { "border-foreground bg-marker/25": over },
      )}
      ondragover={(event) => {
        event.preventDefault();
        over = true;
      }}
      ondragleave={() => (over = false)}
      ondrop={dropped}
    >
      <span class="text-sm font-semibold">{m.upload_choose()}</span>
      <span class="text-xs text-muted-foreground">{m.upload_limits()}</span>
      <input
        class="sr-only"
        type="file"
        accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.csv,.xlsx,.ods,.zip,.gpkg,.parquet,.geojson,application/pdf,image/png,image/jpeg,image/tiff"
        onchange={chosen}
      />
    </label>
    <label class="flex items-center gap-2 text-xs text-muted-foreground">
      <Checkbox bind:checked={shared} />
      {m.upload_public()}
    </label>
    {#if problem != null}
      <p class="text-sm text-pretty text-destructive" role="alert">
        {problem}
      </p>
    {/if}
  </div>
{/if}

<style>
  @media (prefers-reduced-motion: no-preference) {
    .upload-reading {
      animation: upload-reading 1.4s ease-in-out infinite;
    }
  }

  @keyframes upload-reading {
    50% {
      opacity: 0.35;
    }
  }
</style>
