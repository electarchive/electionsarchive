<script lang="ts">
  import { resolve } from "$app/paths";
  import type { Found } from "#lib/workspace.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  let {
    value = $bindable(),
    id,
    label,
    isLabelShown = true,
    placeholder = m.ws_record_placeholder(),
    isLocked,
    state,
    note = null,
    onfocus,
    onpaste,
  }: {
    value: string;
    id: string;
    label: string;
    isLabelShown?: boolean;
    placeholder?: string;
    isLocked: boolean;
    state: { entity: Found | null; problem: string | null; isLooking: boolean };
    note?: string | null;
    onfocus?: () => void;
    onpaste?: (event: ClipboardEvent) => void;
  } = $props();
</script>

<div class="grid content-start gap-1 min-inline-0">
  <label
    for={id}
    class={cn("text-xs text-muted-foreground", { "sr-only": !isLabelShown })}
    >{label}</label
  >
  <Input
    {id}
    bind:value
    disabled={isLocked}
    {placeholder}
    aria-invalid={state.problem != null}
    autocomplete="off"
    spellcheck={false}
    maxlength={2048}
    class="block-8"
    {onfocus}
    {onpaste}
  />
  {#if state.problem != null}
    <p class="text-xs font-semibold text-pretty text-destructive">
      {state.problem}
    </p>
  {:else if state.isLooking}
    <p class="text-xs text-muted-foreground">{m.ws_record_looking()}</p>
  {:else if state.entity != null}
    <p class="truncate text-xs">
      <a
        class="font-semibold underline decoration-border underline-offset-4"
        href={resolve("/id/[id]", { id: state.entity.id })}
        target="_blank"
        rel="noopener">{state.entity.name}</a
      >{#if state.entity.polity != null && state.entity.type !== "polity"}<span
          class="text-muted-foreground">, {state.entity.polity}</span
        >{/if}
    </p>
  {:else if note != null}
    <p class="truncate text-xs text-muted-foreground">{note}</p>
  {/if}
</div>
