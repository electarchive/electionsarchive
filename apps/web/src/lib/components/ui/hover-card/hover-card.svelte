<script lang="ts" module>
  let active = $state<symbol | null>(null);
  let isPrefetched = false;

  const prefetch = () => {
    if (isPrefetched) {
      return;
    }
    isPrefetched = true;
    const load = () => void import("./hover-card-body.svelte");
    if ("requestIdleCallback" in window) {
      requestIdleCallback(load);
    } else {
      setTimeout(load, 1000);
    }
  };
</script>

<script lang="ts">
  import { LinkPreview as HoverCardPrimitive } from "bits-ui";
  import { onMount, untrack } from "svelte";

  let { open = $bindable(false), ...restProps }: HoverCardPrimitive.RootProps =
    $props();

  onMount(prefetch);

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
