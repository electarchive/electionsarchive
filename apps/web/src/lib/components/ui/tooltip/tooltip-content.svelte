<script lang="ts">
  import type { ComponentProps } from "svelte";
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import type { WithoutChildrenOrChild } from "#lib/utils.js";
  import { cn } from "#lib/utils.js";
  import TooltipPortal from "./tooltip-portal.svelte";

  let {
    ref = $bindable(null),
    class: className,
    sideOffset = 0,
    side = "top",
    children,
    arrowClasses,
    portalProps,
    ...restProps
  }: TooltipPrimitive.ContentProps & {
    arrowClasses?: string;
    portalProps?: WithoutChildrenOrChild<ComponentProps<typeof TooltipPortal>>;
  } = $props();
</script>

<TooltipPortal {...portalProps}>
  <TooltipPrimitive.Content
    bind:ref
    data-slot="tooltip-content"
    {sideOffset}
    {side}
    class={cn(
      "z-50 inline-flex w-fit max-w-xs origin-(--bits-tooltip-content-transform-origin) items-center gap-1.5 rounded-sm bg-foreground px-2 py-1 text-[0.8125rem] leading-tight font-medium text-background [--muted-foreground:color-mix(in_oklab,var(--background)_72%,var(--foreground))] data-[state=delayed-open]:animate-in data-[state=delayed-open]:fade-in-0 motion-reduce:animate-none data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0 data-closed:fill-mode-forwards **:[kbd]:border-background/30 **:[kbd]:text-background/80",
      className,
    )}
    {...restProps}
  >
    {@render children?.()}
    <TooltipPrimitive.Arrow>
      {#snippet child({ props })}
        <div
          class={cn(
            "z-50 size-2.5 translate-y-[calc(-50%_-_2px)] rotate-45 rounded-[2px] bg-foreground fill-foreground data-[side=bottom]:-translate-x-1/2 data-[side=bottom]:-translate-y-[calc(-50%_+_1px)] data-[side=left]:-translate-y-[calc(50%_-_3px)] data-[side=right]:translate-x-[calc(50%_+_2px)] data-[side=right]:translate-y-1/2 data-[side=top]:translate-x-1/2 data-[side=top]:translate-y-[calc(-50%_+_2px)]",
            arrowClasses,
          )}
          {...props}
        ></div>
      {/snippet}
    </TooltipPrimitive.Arrow>
  </TooltipPrimitive.Content>
</TooltipPortal>
