<script lang="ts">
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { cn } from "#lib/utils.js";

  const {
    label,
    definition,
    class: className,
  }: { label: string; definition: string; class?: string } = $props();
</script>

<Tooltip.Root disableCloseOnTriggerClick delayDuration={250}>
  <Tooltip.Trigger
    class={cn(
      "cursor-help text-start underline decoration-current/35 decoration-dotted decoration-1 underline-offset-[0.25em] hover:decoration-current",
      className,
    )}>{label}</Tooltip.Trigger
  >
  <Tooltip.Content collisionPadding={8} class="leading-snug max-inline-64"
    >{definition}</Tooltip.Content
  >
</Tooltip.Root>
