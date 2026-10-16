<script lang="ts" module>
  import type {
    HTMLAnchorAttributes,
    HTMLButtonAttributes,
  } from "svelte/elements";
  import type { VariantProps } from "tailwind-variants";
  import { tv } from "tailwind-variants";
  import type { WithElementRef } from "#lib/utils.js";
  import { cn } from "#lib/utils.js";

  export const buttonVariants = tv({
    base: "group/button inline-flex shrink-0 items-center justify-center gap-1.5 rounded-sm border border-input bg-card text-sm font-semibold whitespace-nowrap text-foreground outline-none select-none disabled:pointer-events-none disabled:opacity-50 aria-disabled:pointer-events-none aria-disabled:opacity-50 aria-invalid:border-destructive [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
    variants: {
      variant: {
        default:
          "border-foreground bg-marker text-[#15171a] keycap hover:bg-[color-mix(in_oklab,var(--marker),#15171a_10%)]",
        outline:
          "keycap hover:bg-secondary aria-expanded:bg-secondary data-open:bg-secondary",
        secondary: "bg-secondary keycap hover:bg-card",
        ghost:
          "border-transparent bg-transparent hover:highlighter aria-expanded:highlighter data-open:highlighter",
        destructive:
          "border-destructive/60 text-destructive keycap hover:bg-destructive/10",
        link: "border-transparent bg-transparent underline decoration-border underline-offset-4 hover:decoration-foreground",
      },
      size: {
        default: "h-8 px-3",
        xs: "h-6 gap-1 px-2 text-xs [&_svg:not([class*='size-'])]:size-3",
        sm: "h-7 gap-1 px-2.5 text-[0.8125rem]",
        lg: "h-9 px-4",
        icon: "size-8",
        "icon-xs": "size-6 [&_svg:not([class*='size-'])]:size-3",
        "icon-sm": "size-7",
        "icon-lg": "size-9",
      },
    },
    compoundVariants: [{ variant: "link", class: "h-auto px-0" }],
    defaultVariants: { variant: "default", size: "default" },
  });

  export type ButtonVariant = VariantProps<typeof buttonVariants>["variant"];
  export type ButtonSize = VariantProps<typeof buttonVariants>["size"];

  export type ButtonProps = WithElementRef<HTMLButtonAttributes> &
    WithElementRef<HTMLAnchorAttributes> & {
      variant?: ButtonVariant;
      size?: ButtonSize;
    };
</script>

<script lang="ts">
  let {
    class: className,
    variant = "default",
    size = "default",
    ref = $bindable(null),
    href = undefined,
    type = "button",
    disabled,
    children,
    ...restProps
  }: ButtonProps = $props();
</script>

{#if href}
  <a
    bind:this={ref}
    data-slot="button"
    class={cn(buttonVariants({ variant, size }), className)}
    href={disabled ? undefined : href}
    aria-disabled={disabled}
    role={disabled ? "link" : undefined}
    tabindex={disabled ? -1 : undefined}
    {...restProps}
  >
    {@render children?.()}
  </a>
{:else}
  <button
    bind:this={ref}
    data-slot="button"
    class={cn(buttonVariants({ variant, size }), className)}
    {type}
    {disabled}
    {...restProps}
  >
    {@render children?.()}
  </button>
{/if}
