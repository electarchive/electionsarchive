<script lang="ts">
  import PartyCard from "#lib/components/party-card.svelte";
  import PersonCard from "#lib/components/person-card.svelte";
  import * as HoverCard from "#lib/components/ui/hover-card/index.js";
  import { cn } from "#lib/utils.js";

  const {
    label,
    color = null,
    person = null,
    party = null,
    class: className,
  }: {
    label: string;
    color?: string | null;
    person?: string | null;
    party?: string | null;
    class?: string;
  } = $props();

  let open = $state(false);
</script>

{#snippet face()}
  {#if color != null}
    <span
      class="me-[0.35em] inline-block shrink-0 rounded-[0.15em] bg-(--swatch) align-[-0.02em] block-[0.62em] inline-[0.62em]"
      style:--swatch={color}
      aria-hidden="true"
    ></span>
  {/if}{label}
{/snippet}

{#if person == null && party == null}
  <span class={className}>{@render face()}</span>
{:else}
  <HoverCard.Root bind:open openDelay={280} closeDelay={120}>
    <HoverCard.Trigger>
      {#snippet child({ props })}
        <button
          {...props}
          type="button"
          class={cn(
            "cursor-default text-start underline decoration-transparent decoration-1 underline-offset-[0.22em] transition-[text-decoration-color] hover:decoration-current focus-visible:decoration-current",
            className,
          )}
          onclick={(event) => {
            if (
              !(event instanceof PointerEvent) ||
              event.pointerType !== "mouse"
            ) {
              open = !open;
            }
          }}>{@render face()}</button
        >
      {/snippet}
    </HoverCard.Trigger>
    <HoverCard.Content
      align="start"
      class="p-0 text-start font-sans text-sm font-normal tracking-normal text-foreground normal-case inline-[min(21rem,calc(100vw-2rem))]"
    >
      {#if person == null}
        <PartyCard id={party ?? ""} />
      {:else}
        <PersonCard id={person} />
      {/if}
    </HoverCard.Content>
  </HoverCard.Root>
{/if}
