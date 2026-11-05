<script lang="ts">
  import type { LiveOption } from "@ea/analytics/live";
  import Name from "#lib/components/name.svelte";
  import { count } from "#lib/format.js";
  import { tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import Face from "./face.svelte";

  const {
    options,
    isTacit,
    seats,
  }: { options: Array<LiveOption>; isTacit: boolean; seats: number } = $props();
</script>

<div class="grid gap-5 border-bs-2 border-foreground pbs-5 max-inline-[78ch]">
  <ul class="grid gap-3">
    {#each options as option (option.id)}
      <li class="flex items-center gap-3">
        <Face
          {option}
          color={tone(option)}
          class="block-10 inline-10 sm:block-11 sm:inline-11"
        />
        <span class="grid gap-0.5 min-inline-0">
          <Name
            label={option.label}
            person={option.person}
            hasSwatch={false}
            class="truncate text-base/tight font-[720] tracking-tight"
          />
          {#if option.party != null}
            <span class="truncate text-xs text-muted-foreground">
              <Name
                label={option.party.name}
                party={option.party.id}
                color={option.party.color}
                hasSwatch={false}
              />
            </span>
          {/if}
        </span>
      </li>
    {/each}
  </ul>
  <div class="grid gap-2">
    <h2 class="heading text-lg!">{m.night_unopposed_title()}</h2>
    <p class="text-[0.9375rem]/relaxed text-pretty">
      {isTacit ? m.night_unopposed_tacit() : m.night_unopposed_counted()}
    </p>
    {#if options.length < seats}
      <p class="font-figures text-sm text-muted-foreground tabular-nums">
        {m.night_unopposed_open({ count: count(seats - options.length) })}
      </p>
    {/if}
  </div>
</div>
