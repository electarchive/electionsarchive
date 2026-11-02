<script lang="ts">
  import { onMount } from "svelte";
  import { cn } from "#lib/utils.js";

  let {
    value = $bindable(),
    min,
    max,
    step,
    storage,
    label,
    measure,
    reverse = false,
    class: className,
    style,
  }: {
    value: number;
    min: number;
    max: number;
    step: number;
    storage: string;
    label: string;
    measure: (x: number) => number | null;
    reverse?: boolean;
    class?: string;
    style?: string;
  } = $props();

  let dragging = $state(false);

  const clamp = (next: number) => Math.min(max, Math.max(min, next));

  function save() {
    try {
      localStorage.setItem(storage, String(Math.round(value)));
    } catch {}
  }

  onMount(() => {
    try {
      const saved = Number(localStorage.getItem(storage) ?? Number.NaN);
      if (Number.isFinite(saved)) {
        value = clamp(saved);
      }
    } catch {}
  });

  function onkeydown(event: KeyboardEvent) {
    const nudge = reverse ? -step : step;
    const next =
      event.key === "ArrowLeft"
        ? value - nudge
        : event.key === "ArrowRight"
          ? value + nudge
          : event.key === "Home"
            ? min
            : event.key === "End"
              ? max
              : null;
    if (next == null) {
      return;
    }
    event.preventDefault();
    value = clamp(next);
    save();
  }
</script>

<div
  role="slider"
  tabindex="0"
  aria-orientation="horizontal"
  aria-label={label}
  aria-valuenow={Math.round(value)}
  aria-valuemin={min}
  aria-valuemax={max}
  class={cn(
    "group grid cursor-col-resize touch-none place-items-center outline-none",
    className,
  )}
  {style}
  onpointerdown={(event) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragging = true;
  }}
  onpointermove={(event) => {
    const next = dragging ? measure(event.clientX) : null;
    if (next != null) {
      value = clamp(next);
    }
  }}
  onpointerup={() => {
    dragging = false;
    save();
  }}
  onpointercancel={() => (dragging = false)}
  {onkeydown}
>
  <span
    class={cn(
      "bg-border block-full inline-px group-hover:bg-foreground group-focus-visible:bg-ring group-focus-visible:inline-0.5",
      { "bg-foreground": dragging },
    )}
  ></span>
</div>
