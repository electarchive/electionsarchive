<script lang="ts" module>
  import type ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";

  export type SegmentedOption<T extends string | number> = {
    value: T;
    label: string;
    icon?: typeof ChevronLeftIcon;
    count?: number | string;
    title?: string;
    tip?: string;
    href?: string;
    disabled?: boolean;
    isIconOnly?: boolean;
  };
</script>

<script lang="ts" generics="T extends string | number">
  import { mergeProps, RadioGroup, Tooltip as TooltipPrimitive } from "bits-ui";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { cn } from "#lib/utils.js";

  let {
    options,
    value = $bindable(),
    label,
    size = "md",
    fill = false,
    class: className,
    onValueChange,
  }: {
    options: Array<SegmentedOption<T>>;
    value?: T;
    label: string;
    size?: "sm" | "md";
    fill?: boolean;
    class?: string;
    onValueChange?: (value: T) => void;
  } = $props();

  const isNav = $derived(options.some((option) => option.href != null));
  const hasTips = $derived(options.some((option) => option.tip != null));
  const tether = TooltipPrimitive.createTether<string>();

  function choose(next: string) {
    const found = options.find((option) => String(option.value) === next);
    if (found == null || found.value === value) {
      return;
    }
    value = found.value;
    onValueChange?.(found.value);
  }
</script>

{#snippet face(option: SegmentedOption<T>)}
  {#if option.icon != null}
    <option.icon
      class={cn("shrink-0", {
        "block-3 inline-3": size === "sm",
        "block-3.5 inline-3.5": size === "md",
      })}
      aria-hidden="true"
    />
  {/if}
  {#if option.isIconOnly}
    <span class="sr-only">{option.label}</span>
  {:else}
    <span class="truncate">{option.label}</span>
  {/if}
  {#if option.count != null}
    <span
      class="font-figures font-medium text-muted-foreground tabular-nums group-aria-[current=page]/segment:text-primary-foreground/70 group-data-[state=checked]/segment:text-primary-foreground/70"
      >{option.count}</span
    >
  {/if}
{/snippet}

{#snippet link(option: SegmentedOption<T>, extra: Record<string, unknown>)}
  {const isOn = $derived(option.value === value)}
  <a
    {...mergeProps(extra, { onclick: () => choose(String(option.value)) })}
    href={option.disabled ? undefined : option.href}
    aria-current={isOn ? "page" : undefined}
    aria-disabled={option.disabled ? true : undefined}
    title={option.title ?? (option.isIconOnly ? option.label : undefined)}
    data-sveltekit-reset="false"
    class={cn(
      "group/segment -ms-px inline-flex shrink-0 items-center justify-center gap-1.5 border border-input bg-card font-semibold whitespace-nowrap keycap outline-none first:ms-0 first:rounded-s-sm last:rounded-e-sm focus-visible:z-20 focus-visible:ring-2 focus-visible:ring-ring/50 pointer-coarse:block-10",
      {
        "px-2 text-xs block-6": size === "sm",
        "px-2.5 text-[0.8125rem] block-7": size === "md",
        "flex-1": fill,
      },
      {
        "relative z-10 border-primary bg-primary text-primary-foreground": isOn,
        "hover:bg-secondary": !isOn,
        "pointer-events-none opacity-40": option.disabled,
      },
    )}
  >
    {@render face(option)}
  </a>
{/snippet}

{#snippet radio(option: SegmentedOption<T>, extra: Record<string, unknown>)}
  <RadioGroup.Item
    {...extra}
    value={String(option.value)}
    disabled={option.disabled}
    title={option.title ?? (option.isIconOnly ? option.label : undefined)}
    class={cn(
      "group/segment -ms-px inline-flex shrink-0 items-center justify-center gap-1.5 border border-input bg-card font-semibold whitespace-nowrap keycap outline-none first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary focus-visible:z-20 focus-visible:ring-2 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-40 data-[state=checked]:relative data-[state=checked]:z-10 data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground pointer-coarse:block-10",
      {
        "px-2 text-xs block-6": size === "sm",
        "px-2.5 text-[0.8125rem] block-7": size === "md",
        "flex-1": fill,
      },
    )}
  >
    {@render face(option)}
  </RadioGroup.Item>
{/snippet}

{#snippet items(item: typeof link)}
  {#if hasTips}
    <Tooltip.Root disableCloseOnTriggerClick {tether} delayDuration={300}>
      {#snippet children({ payload })}
        {#each options as option (option.value)}
          {#if option.tip == null}
            {@render item(option, {})}
          {:else}
            <Tooltip.Trigger {tether} payload={option.tip}>
              {#snippet child({ props })}
                {@render item(option, props)}
              {/snippet}
            </Tooltip.Trigger>
          {/if}
        {/each}
        <Tooltip.Content collisionPadding={8} class="leading-snug max-inline-64"
          >{payload}</Tooltip.Content
        >
      {/snippet}
    </Tooltip.Root>
  {:else}
    {#each options as option (option.value)}
      {@render item(option, {})}
    {/each}
  {/if}
{/snippet}

{#if isNav}
  <nav
    aria-label={label}
    class={cn(
      "scroll-fade-x flex max-inline-full",
      { "inline-full": fill, "inline-flex": !fill },
      className,
    )}
  >
    {@render items(link)}
  </nav>
{:else}
  <RadioGroup.Root
    value={value == null ? "" : String(value)}
    onValueChange={choose}
    orientation="horizontal"
    loop
    aria-label={label}
    class={cn(
      "scroll-fade-x flex max-inline-full",
      { "inline-full": fill, "inline-flex": !fill },
      className,
    )}
  >
    {@render items(radio)}
  </RadioGroup.Root>
{/if}
