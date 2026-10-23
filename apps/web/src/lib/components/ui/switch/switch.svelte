<script lang="ts">
  import { Switch as SwitchPrimitive } from "bits-ui";
  import type { WithoutChildrenOrChild } from "#lib/utils.js";
  import { cn } from "#lib/utils.js";

  let {
    ref = $bindable(null),
    class: className,
    checked = $bindable(false),
    size = "default",
    ...restProps
  }: WithoutChildrenOrChild<SwitchPrimitive.RootProps> & {
    size?: "sm" | "default";
  } = $props();
</script>

<SwitchPrimitive.Root
  bind:ref
  bind:checked
  data-slot="switch"
  data-size={size}
  class={cn(
    "peer group/switch relative inline-flex shrink-0 items-center rounded-xs border border-input bg-secondary p-px transition-colors after:absolute after:-inset-x-3 after:-inset-y-2 aria-invalid:border-destructive data-[size=default]:h-5 data-[size=default]:w-9 data-[size=sm]:h-4 data-[size=sm]:w-7 motion-reduce:transition-none data-checked:border-foreground data-checked:bg-primary data-disabled:cursor-not-allowed data-disabled:opacity-50",
    className,
  )}
  {...restProps}
>
  <SwitchPrimitive.Thumb
    data-slot="switch-thumb"
    class={cn(
      "pointer-events-none block aspect-square h-full rounded-[calc(var(--radius)*0.3)] border border-input bg-card transition-transform motion-reduce:transition-none data-checked:translate-x-full data-checked:border-foreground rtl:data-checked:-translate-x-full",
    )}
  />
</SwitchPrimitive.Root>
