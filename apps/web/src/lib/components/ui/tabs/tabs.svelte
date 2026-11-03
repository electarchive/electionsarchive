<script lang="ts" module>
  export type TabOption<T extends string> = {
    value: T;
    label: string;
    count?: number | string;
    disabled?: boolean;
  };
</script>

<script lang="ts" generics="T extends string">
  import { cn } from "#lib/utils.js";

  let {
    options,
    value = $bindable(),
    label,
    id,
    panel,
    size = "md",
    class: className,
    onValueChange,
  }: {
    options: Array<TabOption<T>>;
    value?: T;
    label?: string;
    id: string;
    panel?: string;
    size?: "sm" | "md";
    class?: string;
    onValueChange?: (value: T) => void;
  } = $props();

  let list = $state<HTMLElement | null>(null);

  function choose(next: T) {
    if (next === value) {
      return;
    }
    value = next;
    onValueChange?.(next);
  }

  function move(event: KeyboardEvent) {
    const enabled = options.filter((option) => option.disabled !== true);
    const at = enabled.findIndex((option) => option.value === value);
    let next: TabOption<T> | undefined;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      next = enabled[(at + 1) % enabled.length];
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      next = enabled[(at - 1 + enabled.length) % enabled.length];
    } else if (event.key === "Home") {
      next = enabled[0];
    } else if (event.key === "End") {
      next = enabled.at(-1);
    }
    if (next == null) {
      return;
    }
    event.preventDefault();
    choose(next.value);
    list
      ?.querySelector<HTMLElement>(`#${CSS.escape(`${id}-${next.value}`)}`)
      ?.focus();
  }
</script>

<div
  bind:this={list}
  role="tablist"
  aria-label={label}
  tabindex="-1"
  class={cn(
    "scroll-fade-x flex items-end gap-4 shadow-[inset_0_-1px_0_var(--color-border)]",
    { "text-xs": size === "sm", "text-[0.8125rem]": size === "md" },
    className,
  )}
  onkeydown={move}
>
  {#each options as option (option.value)}
    {const isOn = $derived(option.value === value)}
    <button
      type="button"
      role="tab"
      id="{id}-{option.value}"
      aria-selected={isOn}
      aria-controls={panel}
      tabindex={isOn ? 0 : -1}
      disabled={option.disabled}
      class={cn(
        "inline-flex shrink-0 items-center gap-1.5 border-be-2 border-transparent whitespace-nowrap outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-40",
        { "pbe-1": size === "sm", "pbs-1 pbe-1.5": size === "md" },
        {
          "border-foreground font-semibold text-foreground": isOn,
          "text-muted-foreground hover:text-foreground": !isOn,
        },
      )}
      onclick={() => choose(option.value)}
    >
      {option.label}
      {#if option.count != null}
        <span class="font-figures text-muted-foreground tabular-nums"
          >{option.count}</span
        >
      {/if}
    </button>
  {/each}
</div>
