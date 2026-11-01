<script lang="ts">
  import type { Snippet } from "svelte";
  import * as HoverCard from "#lib/components/ui/hover-card/index.js";
  import { isTouch } from "#lib/explain.svelte.js";

  const {
    href,
    title,
    class: className,
    style,
    children,
    card,
    onTouch,
  }: {
    href: string;
    title: string;
    class: string;
    style?: string;
    children: Snippet;
    card: Snippet;
    onTouch: () => void;
  } = $props();
</script>

<HoverCard.Root openDelay={120} closeDelay={60}>
  <HoverCard.Trigger>
    {#snippet child({ props })}
      <a
        {...props}
        {href}
        class={className}
        {style}
        aria-label={title}
        onclick={(event) => {
          if (isTouch(event)) {
            event.preventDefault();
            onTouch();
          }
        }}
        role={undefined}
        aria-haspopup={undefined}
        aria-expanded={undefined}>{@render children()}</a
      >
    {/snippet}
  </HoverCard.Trigger>
  <HoverCard.Content
    collisionPadding={8}
    class="grid gap-2.5 inline-[min(19rem,calc(100vw-2rem))]"
  >
    {@render card()}
  </HoverCard.Content>
</HoverCard.Root>
