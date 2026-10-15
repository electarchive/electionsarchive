<script lang="ts" module>
  let active = $state<symbol | null>(null);
</script>

<script lang="ts">
  import { LinkPreview as HoverCardPrimitive } from "bits-ui";

  let { open = $bindable(false), ...restProps }: HoverCardPrimitive.RootProps =
    $props();

  const id = Symbol("hover-card");

  $effect(() => {
    if (active !== id) {
      open = false;
    }
  });
</script>

<HoverCardPrimitive.Root
  bind:open={
    () => open && active === id,
    (value) => {
      open = value;
      if (value) {
        active = id;
      } else if (active === id) {
        active = null;
      }
    }
  }
  {...restProps}
/>
