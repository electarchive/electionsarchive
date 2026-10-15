<script lang="ts" module>
  let active = $state<symbol | null>(null);
</script>

<script lang="ts">
  import { LinkPreview as HoverCardPrimitive } from "bits-ui";
  import { untrack } from "svelte";

  let { open = $bindable(false), ...restProps }: HoverCardPrimitive.RootProps =
    $props();

  const id = Symbol("hover-card");

  $effect.pre(() => {
    if (open) {
      active = id;
    }
  });

  $effect.pre(() => {
    if (active !== id && untrack(() => open)) {
      open = false;
    }
  });
</script>

<HoverCardPrimitive.Root bind:open {...restProps} />
