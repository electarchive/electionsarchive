<script lang="ts">
  import type { MomentStyle } from "#lib/zone.svelte.js";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { instant, moment, utc, zone } from "#lib/zone.svelte.js";

  const {
    at,
    style = "full",
    label,
    class: className,
  }: {
    at: string;
    style?: MomentStyle;
    label?: string;
    class?: string;
  } = $props();

  const text = $derived(label ?? moment(at, style));
  const iso = $derived(instant(at).toISOString());
  const isLocal = $derived(zone.name !== "UTC");
  const hints = $derived(
    style === "date" && isLocal ? [moment(at), utc(at)] : [utc(at)],
  );
</script>

{#if isLocal || style === "date"}
  <Tooltip.Root delayDuration={250}>
    <Tooltip.Trigger tabindex={-1}>
      {#snippet child({ props }: { props: Record<string, unknown> })}
        <time {...props} type={undefined} datetime={iso} class={className}
          >{text}</time
        >
      {/snippet}
    </Tooltip.Trigger>
    <Tooltip.Content collisionPadding={8} class="grid gap-0.5 font-figures">
      {#each hints as hint (hint)}
        <span>{hint}</span>
      {/each}
    </Tooltip.Content>
  </Tooltip.Root>
{:else}
  <time datetime={iso} class={className}>{text}</time>
{/if}
