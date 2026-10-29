<script lang="ts">
  import type { Logo } from "#lib/logo.js";
  import { logoCredit } from "#lib/logo.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    logo,
    label,
    kind,
    size,
    isEager = false,
    class: className,
  }: {
    logo: Logo;
    label: string;
    kind: "mark" | "lockup";
    size: number;
    isEager?: boolean;
    class?: string;
  } = $props();
</script>

<img
  class={cn(
    "shrink-0 object-contain",
    { "dark:rounded-[3px] dark:bg-white": !logo.isPlate },
    className,
  )}
  src={kind === "mark" ? logo.mark : logo.src}
  alt={m.logo_alt({ party: label })}
  title={logoCredit(logo)}
  width={kind === "mark" ? size : Math.round((size * logo.width) / logo.height)}
  height={size}
  loading={isEager ? "eager" : "lazy"}
  decoding="async"
/>
