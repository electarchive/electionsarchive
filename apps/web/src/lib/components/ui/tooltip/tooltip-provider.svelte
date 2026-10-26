<script lang="ts">
  import { Tooltip as TooltipPrimitive } from "bits-ui";

  let {
    delayDuration = 200,
    disableHoverableContent = true,
    ignoreNonKeyboardFocus = true,
    ...restProps
  }: TooltipPrimitive.ProviderProps = $props();
</script>

<TooltipPrimitive.Provider
  {delayDuration}
  {disableHoverableContent}
  {ignoreNonKeyboardFocus}
  {...restProps}
/>
