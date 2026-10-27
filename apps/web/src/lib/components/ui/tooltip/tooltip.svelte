<script lang="ts" module>
  import { isTouch } from "#lib/explain.svelte.js";

  let pressed: DOMRect | null = null;
  let isWatching = false;

  const watch = () => {
    if (isWatching) {
      return;
    }
    isWatching = true;
    const options = { capture: true, passive: true };
    document.addEventListener(
      "pointerdown",
      (event) => {
        const trigger =
          event.target instanceof Element && !isTouch(event)
            ? event.target.closest("[data-tooltip-trigger]")
            : null;
        pressed = trigger?.getBoundingClientRect() ?? null;
      },
      options,
    );
    document.addEventListener(
      "pointermove",
      (event) => {
        if (
          pressed != null &&
          (event.clientX < pressed.left - 2 ||
            event.clientX > pressed.right + 2 ||
            event.clientY < pressed.top - 2 ||
            event.clientY > pressed.bottom + 2)
        ) {
          pressed = null;
        }
      },
      options,
    );
    document.addEventListener(
      "keydown",
      () => {
        pressed = null;
      },
      options,
    );
  };
</script>

<script lang="ts" generics="T = never">
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import { onMount } from "svelte";

  let {
    open = $bindable(false),
    ignoreNonKeyboardFocus = true,
    ...restProps
  }: TooltipPrimitive.RootProps<T> = $props();

  onMount(watch);
</script>

<TooltipPrimitive.Root
  bind:open={
    () => open,
    (next) => {
      if (!next || pressed == null) {
        open = next;
      }
    }
  }
  {ignoreNonKeyboardFocus}
  {...restProps}
/>
