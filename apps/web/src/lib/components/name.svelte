<script lang="ts">
  import type { Snippet } from "svelte";
  import { resolve } from "$app/paths";
  import { getContext } from "svelte";
  import PartyCard from "#lib/components/party-card.svelte";
  import PersonCard from "#lib/components/person-card.svelte";
  import * as HoverCard from "#lib/components/ui/hover-card/index.js";
  import { isTouch, show } from "#lib/explain.svelte.js";
  import { WITHIN } from "#lib/party.js";
  import { cn } from "#lib/utils.js";

  const {
    label,
    color = null,
    hasSwatch = true,
    person = null,
    party = null,
    tabindex,
    class: className,
    children,
  }: {
    label: string;
    color?: string | null;
    hasSwatch?: boolean;
    person?: string | null;
    party?: string | null;
    tabindex?: number;
    class?: string;
    children?: Snippet;
  } = $props();

  const scope = getContext<(() => string | null) | undefined>(WITHIN);
</script>

{#snippet face()}
  {#if hasSwatch && color != null}
    <span
      class="me-[0.35em] inline-block shrink-0 rounded-[0.15em] bg-(--swatch) align-[-0.02em] chip block-[0.62em] inline-[0.62em]"
      style:--swatch={color}
      aria-hidden="true"
    ></span>
  {/if}{label}
{/snippet}

{#if person == null && party == null}
  <span class={className}>{@render (children ?? face)()}</span>
{:else}
  <HoverCard.Root openDelay={280} closeDelay={120}>
    <HoverCard.Trigger>
      {#snippet child({ props })}
        <a
          {...props}
          href={resolve("/id/[id]", { id: person ?? party ?? "" })}
          {tabindex}
          class={cn(
            "underline decoration-transparent decoration-1 underline-offset-[0.22em] transition-[text-decoration-color] hover:decoration-current focus-visible:decoration-current",
            className,
          )}
          onclick={(event) => {
            if (isTouch(event)) {
              show(
                event,
                person == null
                  ? {
                      kind: "party",
                      id: party ?? "",
                      color,
                      within: scope?.() ?? null,
                    }
                  : { kind: "person", id: person },
              );
            }
          }}
          role={undefined}
          aria-haspopup={undefined}
          aria-expanded={undefined}>{@render (children ?? face)()}</a
        >
      {/snippet}
    </HoverCard.Trigger><HoverCard.Content
      collisionPadding={8}
      align="start"
      class="p-0 text-start font-sans text-sm font-normal tracking-normal text-foreground normal-case inline-[min(21rem,calc(100vw-2rem))]"
    >
      {#if person == null}
        <PartyCard id={party ?? ""} {color} within={scope?.() ?? null} />
      {:else}
        <PersonCard id={person} />
      {/if}
    </HoverCard.Content>
  </HoverCard.Root>
{/if}
