<script lang="ts">
  import type { LiveOption } from "@ea/analytics/live";
  import { initials } from "#lib/format.js";
  import { tone } from "#lib/live/format.js";
  import { cn } from "#lib/utils.js";

  const {
    option,
    color = null,
    class: className,
  }: {
    option: LiveOption | undefined;
    color?: string | null;
    class?: string;
  } = $props();

  const ring = $derived(color ?? tone(option));
</script>

{#if option != null && (option.photo != null || option.person != null)}
  {#if option.photo != null}
    <img
      class={cn(
        "shrink-0 rounded-full bg-muted object-cover object-top ring-2 ring-(--party) ring-offset-1 ring-offset-card",
        className,
      )}
      style:--party={ring}
      src={option.photo}
      alt=""
      loading="lazy"
    />
  {:else}
    <span
      class={cn(
        "grid shrink-0 place-items-center rounded-full bg-secondary text-[0.625rem] font-semibold text-muted-foreground ring-2 ring-(--party) ring-offset-1 ring-offset-card select-none",
        className,
      )}
      style:--party={ring}
      aria-hidden="true">{initials(option.label)}</span
    >
  {/if}
{/if}
