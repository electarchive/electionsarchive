<script lang="ts">
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import type { Tier } from "#lib/atlas/tiers.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { capitalize, count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    chain,
    choices,
    begins,
    zoom,
    active,
    locked,
    isCompact = false,
    onlock,
  }: {
    chain: Array<Tier>;
    choices: Array<Tier>;
    begins: Array<number>;
    zoom: number;
    active: number;
    locked: string | null;
    isCompact?: boolean;
    onlock: (code: string | null) => void;
  } = $props();

  const others = $derived(
    choices.filter((tier) => !chain.some((link) => link.code === tier.code)),
  );
  const shown = $derived(
    locked == null
      ? chain
      : chain.some((tier) => tier.code === locked)
        ? chain
        : [...chain, ...choices.filter((tier) => tier.code === locked)],
  );
  const needle = $derived.by(() => {
    if (locked != null) {
      return Math.max(
        0,
        shown.findIndex((tier) => tier.code === locked),
      );
    }
    let at = 0;
    for (const [index, start] of begins.entries()) {
      const next = begins[index + 1];
      const from = Number.isFinite(start) ? start : (next ?? zoom) - 2.5;
      if (zoom < from) {
        break;
      }
      at =
        next == null
          ? index
          : index + Math.min(1, (zoom - from) / Math.max(0.1, next - from));
    }
    return Math.min(shown.length - 1, at);
  });
  const current = $derived(
    shown.find((tier) => tier.code === locked) ?? chain[active] ?? null,
  );
  const name = (noun: string) => capitalize(noun);
</script>

{#snippet menu()}
  <DropdownMenu.RadioGroup
    value={locked ?? ""}
    onValueChange={(value: string) => onlock(value === "" ? null : value)}
  >
    <DropdownMenu.RadioItem value="">{m.map_tier_auto()}</DropdownMenu.RadioItem
    >
    <DropdownMenu.Separator />
    {#each [...chain, ...others] as tier (tier.code)}
      <DropdownMenu.RadioItem value={tier.code}>
        <span class="flex-1">{name(tier.noun)}</span>
        {#if tier.kind !== "parts"}
          <span class="font-figures text-xs text-muted-foreground tabular-nums"
            >{count(tier.count)}</span
          >
        {/if}
      </DropdownMenu.RadioItem>
    {/each}
  </DropdownMenu.RadioGroup>
{/snippet}

{#if isCompact}
  <DropdownMenu.Root>
    <DropdownMenu.Trigger
      class="flex items-center gap-1.5 rounded-sm bg-card/95 ps-2.5 pe-1.5 text-[0.8125rem] font-semibold shadow-menu ring-1 ring-border block-9"
      aria-label={m.map_tiers()}
    >
      {#if locked == null}
        <span class="text-muted-foreground">{m.map_tier_auto()}</span>
      {/if}
      {name(current?.noun ?? "")}
      <ChevronDownIcon class="text-muted-foreground block-4 inline-4" />
    </DropdownMenu.Trigger>
    <DropdownMenu.Content align="end" class="min-inline-48">
      {@render menu()}
    </DropdownMenu.Content>
  </DropdownMenu.Root>
{:else}
  <nav
    class="grid gap-1 rounded-sm bg-card/95 p-1.5 shadow-menu ring-1 ring-border inline-52"
    aria-label={m.map_tiers()}
  >
    <div class="flex items-center justify-between gap-2 ps-1.5">
      <span class="text-xs font-semibold text-muted-foreground"
        >{m.map_tiers()}</span
      >
      <button
        type="button"
        class={cn(
          "rounded-xs px-1.5 text-xs font-semibold block-6 hover:bg-secondary",
          { "bg-foreground text-card hover:bg-foreground": locked == null },
        )}
        aria-pressed={locked == null}
        onclick={() => onlock(null)}>{m.map_tier_auto()}</button
      >
    </div>
    <ol class="relative grid">
      <span
        class="pointer-events-none absolute inset-s-3.75 bg-border inline-px"
        style:inset-block="1rem"
        aria-hidden="true"
      ></span>
      <span
        class="pointer-events-none absolute inset-s-2.75 z-1 rounded-full bg-foreground transition-[translate] duration-150 ease-out block-0.5 inline-2.5 motion-reduce:transition-none"
        style:inset-block-start="1rem"
        style:translate="0 calc({needle} * 2rem - 1px)"
        aria-hidden="true"
      ></span>
      {#each shown as tier, index (tier.code)}
        {const isOn = $derived(
          locked == null ? index === active : tier.code === locked,
        )}
        <li>
          <button
            type="button"
            class={cn(
              "relative flex items-center gap-2.5 rounded-xs ps-2 pe-1.5 text-start text-[0.8125rem] block-8 inline-full hover:bg-secondary",
              { "font-semibold": isOn },
            )}
            aria-pressed={tier.code === locked}
            title={m.map_tier_lock({ noun: tier.noun })}
            onclick={() => onlock(tier.code === locked ? null : tier.code)}
          >
            <span
              class={cn(
                "z-2 grid shrink-0 place-items-center rounded-full ring-1 block-3.5 inline-3.5",
                {
                  "bg-card ring-input": !isOn,
                  "bg-foreground ring-foreground": isOn,
                },
              )}
              aria-hidden="true"
            >
              {#if tier.code === locked}
                <span class="rounded-full bg-card block-1.5 inline-1.5"></span>
              {/if}
            </span>
            <span class="flex-1 truncate">{name(tier.noun)}</span>
            {#if tier.kind !== "parts"}
              <span
                class="font-figures text-xs text-muted-foreground tabular-nums"
                >{count(tier.count)}</span
              >
            {/if}
          </button>
        </li>
      {/each}
    </ol>
    {#if others.length > 0}
      <DropdownMenu.Root>
        <DropdownMenu.Trigger
          class="flex items-center gap-1 rounded-xs ps-2 text-xs font-semibold text-muted-foreground block-7 hover:bg-secondary hover:text-foreground"
        >
          {m.map_tier_more({ count: others.length })}
          <ChevronDownIcon class="block-3.5 inline-3.5" />
        </DropdownMenu.Trigger>
        <DropdownMenu.Content align="end" class="min-inline-52">
          {@render menu()}
        </DropdownMenu.Content>
      </DropdownMenu.Root>
    {/if}
  </nav>
{/if}
