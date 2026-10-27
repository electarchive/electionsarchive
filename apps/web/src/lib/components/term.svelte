<script lang="ts">
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { isTouch, show } from "#lib/explain.svelte.js";
  import { cn } from "#lib/utils.js";

  const {
    label,
    definition,
    class: className,
    side,
    align,
  }: {
    label: string;
    definition: string;
    class?: string;
    side?: "top" | "bottom";
    align?: "start" | "center";
  } = $props();
</script>

<Tooltip.Root disableCloseOnTriggerClick delayDuration={250}>
  <Tooltip.Trigger
    class={cn(
      "cursor-help text-start underline decoration-current/35 decoration-dotted decoration-1 underline-offset-[0.25em] hover:decoration-current",
      className,
    )}
    onclick={(event: MouseEvent) => {
      if (isTouch(event)) {
        show(event, { kind: "term", label, definition });
      }
    }}>{label}</Tooltip.Trigger
  >
  <Tooltip.Content
    {side}
    {align}
    collisionPadding={8}
    class="leading-snug max-inline-64">{definition}</Tooltip.Content
  >
</Tooltip.Root>
