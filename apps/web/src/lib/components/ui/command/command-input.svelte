<script lang="ts">
  import SearchIcon from "@lucide/svelte/icons/search";
  import { Command as CommandPrimitive } from "bits-ui";
  import { cn } from "#lib/utils.js";

  let {
    ref = $bindable(null),
    class: className,
    value = $bindable(""),
    ...restProps
  }: CommandPrimitive.InputProps = $props();
</script>

<div
  data-slot="command-input-wrapper"
  class="flex items-center gap-2 border-b border-border px-2.5 transition-shadow focus-within:shadow-[inset_0_-3px_0_var(--primary)] motion-reduce:transition-none"
>
  <SearchIcon class="size-4 shrink-0 text-muted-foreground" />
  <CommandPrimitive.Input
    bind:ref
    bind:value
    data-slot="command-input"
    class={cn(
      "h-9 w-full min-w-0 bg-transparent text-base outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
      className,
    )}
    {...restProps}
  />
</div>
