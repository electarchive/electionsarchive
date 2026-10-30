<script lang="ts">
  import ChevronLeft from "@lucide/svelte/icons/chevron-left";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import Crop from "@lucide/svelte/icons/crop";
  import ZoomIn from "@lucide/svelte/icons/zoom-in";
  import ZoomOut from "@lucide/svelte/icons/zoom-out";
  import { onDestroy } from "svelte";
  import ScanCrop from "#lib/components/contrib/scan-crop.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Checkbox } from "#lib/components/ui/checkbox/index.js";
  import * as Dialog from "#lib/components/ui/dialog/index.js";
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
  let viewing = $state(false);
  let size = $state<[number, number] | null>(null);
  let image = $state<HTMLImageElement | null>(null);
  const frame = $state({ width: 0, height: 0 });
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
    viewing = false;
    size = null;
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

  const marked = $derived(draft ?? value?.region ?? null);
  const fit = $derived.by(() => {
    if (size == null || frame.width === 0) {
      return null;
    }
    const whole = Math.floor(
      Math.min(frame.width, (frame.height * size[0]) / size[1]),
    );
    return zoomed ? whole * 2 : whole;
  });
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
      {const page = $derived(value.page)}
      {#if value.region != null && size != null}
        <figure class="grid justify-items-start gap-1.5">
          <button
            type="button"
            class="rounded-sm outline-offset-2 max-inline-full focus-visible:outline-[1.5px] focus-visible:outline-ring"
            aria-label={m.upload_edit()}
            onclick={() => (viewing = true)}
          >
            <ScanCrop
              class="hover:ring-foreground"
              href="/uploads/{value.id}/pages/{page}"
              width={size[0]}
              height={size[1]}
              region={value.region}
              label={m.upload_page_alt({ page, filename })}
              limit={240}
            />
          </button>
          <figcaption
            class="flex flex-wrap items-baseline gap-x-3 text-xs text-muted-foreground"
          >
            <span class="font-figures">{m.upload_marked({ page })}</span>
            <button
              type="button"
              class="underline underline-offset-2 hover:text-foreground"
              onclick={() => (viewing = true)}>{m.upload_edit()}</button
            >
          </figcaption>
        </figure>
      {:else}
        <p class="text-xs text-pretty text-muted-foreground">
          {m.upload_unmarked()}
        </p>
        <Button
          variant="outline"
          size="sm"
          class="justify-self-start"
          onclick={() => (viewing = true)}><Crop />{m.upload_open()}</Button
        >
      {/if}
      <Dialog.Root bind:open={viewing}>
        <Dialog.Content
          class="grid-rows-[auto_minmax(0,1fr)_auto] gap-3 overflow-hidden block-[92dvh] sm:inline-[min(96vw,80rem)] sm:max-inline-[min(96vw,80rem)] max-sm:inset-s-0 max-sm:inset-bs-0 max-sm:translate-0 max-sm:rounded-none max-sm:p-3 max-sm:block-dvh max-sm:inline-screen  max-sm:max-block-dvh max-sm:max-inline-none"
        >
          <Dialog.Header class="pe-8">
            <Dialog.Title class="truncate" title={filename}
              >{filename}</Dialog.Title
            >
            <Dialog.Description>{m.upload_mark_hint()}</Dialog.Description>
          </Dialog.Header>
          <div
            class="overflow-auto overscroll-contain rounded-sm bg-muted ring-1 ring-border"
            bind:clientWidth={frame.width}
            bind:clientHeight={frame.height}
          >
            <div
              class="relative mx-auto cursor-crosshair touch-none bg-white select-none"
              style:inline-size={fit == null ? "100%" : `${fit}px`}
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
                src="/uploads/{value.id}/pages/{page}"
                alt={m.upload_page_alt({ page, filename })}
                draggable="false"
                bind:this={image}
                onload={() => {
                  if (image != null) {
                    size = [image.naturalWidth, image.naturalHeight];
                  }
                }}
              />
              {#if marked != null}
                <div
                  class={cn(
                    "pointer-events-none absolute rounded-[2px] bg-marker/55 mix-blend-multiply ring-[1.5px] ring-ring",
                    { "bg-marker/35": draft != null },
                  )}
                  style:inset-inline-start="{marked[0] * 100}%"
                  style:inset-block-start="{marked[1] * 100}%"
                  style:inline-size="{(marked[2] - marked[0]) * 100}%"
                  style:block-size="{(marked[3] - marked[1]) * 100}%"
                ></div>
              {/if}
            </div>
          </div>
          <div
            class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2"
          >
            <div class="flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                class="block-8 inline-8"
                aria-label={m.upload_previous()}
                disabled={page <= 1}
                onclick={() => turn(-1)}><ChevronLeft /></Button
              >
              <span class="font-figures text-sm tabular-nums" aria-live="polite"
                >{m.upload_page({ page, pages: count(pages) })}</span
              >
              <Button
                variant="ghost"
                size="icon"
                class="block-8 inline-8"
                aria-label={m.upload_next()}
                disabled={page >= pages}
                onclick={() => turn(1)}><ChevronRight /></Button
              >
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
            <div
              class="flex items-center gap-3 max-sm:justify-between max-sm:inline-full"
            >
              {#if value.region != null}
                <button
                  type="button"
                  class="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                  onclick={() => {
                    if (value != null) {
                      value = { ...value, region: null };
                    }
                  }}>{m.upload_mark_clear()}</button
                >
              {/if}
              <Dialog.Close>
                {#snippet child({ props })}
                  <Button {...props} size="sm" class="ms-auto"
                    >{m.upload_done()}</Button
                  >
                {/snippet}
              </Dialog.Close>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Root>
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
