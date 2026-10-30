<script lang="ts">
  import { cn } from "#lib/utils.js";
  import type { Box } from "./data.js";
  import { page } from "./data.js";

  type Mark = {
    id: string;
    box: Box;
    label: string;
    state?: "linked" | "finding" | "total";
  };

  const {
    region,
    marks = [],
    selected = null,
    alt,
    class: className,
    onselect,
  }: {
    region: Box;
    marks?: Array<Mark>;
    selected?: string | null;
    alt: string;
    class?: string;
    onselect?: (id: string) => void;
  } = $props();

  const w = $derived(region[2] - region[0]);
  const h = $derived(region[3] - region[1]);

  function place(box: Box) {
    return [
      `inset-inline-start: ${((box[0] - region[0]) / w) * 100}%`,
      `inset-block-start: ${((box[1] - region[1]) / h) * 100}%`,
      `inline-size: ${((box[2] - box[0]) / w) * 100}%`,
      `block-size: ${((box[3] - box[1]) / h) * 100}%`,
    ].join(";");
  }
</script>

<div
  class={cn("relative overflow-clip bg-white", className)}
  style:aspect-ratio="{w * page.width} / {h * page.height}"
>
  <img
    src={page.src}
    {alt}
    class="absolute select-none max-inline-none dark:brightness-[0.88]"
    style:inline-size="{100 / w}%"
    style:block-size="{100 / h}%"
    style:inset-inline-start="{(-region[0] / w) * 100}%"
    style:inset-block-start="{(-region[1] / h) * 100}%"
    draggable="false"
  />
  {#each marks as mark (mark.id)}
    {const active = $derived(mark.id === selected)}
    {#if onselect == null}
      <span
        class={cn("pointer-events-none absolute rounded-[1px]", {
          "bg-marker/40 mix-blend-multiply outline-2 outline-offset-1 outline-[#15171a]":
            active,
          "outline outline-offset-1 outline-[#15171a]/30":
            !active && mark.state !== "finding",
          "outline-2 outline-offset-1 outline-[#15171a] outline-dashed":
            !active && mark.state === "finding",
        })}
        style={place(mark.box)}
      ></span>
    {:else}
      <button
        type="button"
        aria-label={mark.label}
        aria-pressed={active}
        class={cn(
          "absolute rounded-[1px] transition-[background-color,outline-color] duration-100 hover:bg-marker/25 motion-reduce:transition-none",
          {
            "bg-marker/40 outline-2 outline-offset-1 outline-[#15171a]": active,
            "outline outline-offset-1 outline-[#15171a]/25 hover:outline-[#15171a]/60":
              !active && mark.state !== "finding",
            "outline-2 outline-offset-1 outline-[#15171a] outline-dashed":
              !active && mark.state === "finding",
          },
        )}
        style={place(mark.box)}
        onclick={() => onselect(mark.id)}
      ></button>
    {/if}
  {/each}
</div>
