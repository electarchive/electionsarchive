<script lang="ts">
  import { resolve } from "$app/paths";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";

  const {
    parties,
    places,
  }: {
    parties: Array<{
      key: string;
      label: string;
      short: string | null;
      color: string | null;
      lean: number | null;
      seats: number;
      members: Array<{
        place: number;
        name: string | null;
        position: number | null;
      }>;
    }>;
    places: Array<{ name: string; path: string | null }>;
  } = $props();

  const uid = $props.id();

  let party = $state<string | null>(null);
  let query = $state("");
  let isOpen = $state(false);

  const ordered = $derived(
    parties
      .filter((entry) => entry.members.some((member) => member.name != null))
      .toSorted(
        (a, b) =>
          (a.lean ?? Infinity) - (b.lean ?? Infinity) || b.seats - a.seats,
      ),
  );
  const everyone = $derived(
    ordered.flatMap((entry) =>
      entry.members.flatMap((member) =>
        member.name == null
          ? []
          : [
              {
                name: member.name,
                party: entry.key,
                place: places[member.place] ?? null,
                position: member.position,
              },
            ],
      ),
    ),
  );
  const shown = $derived.by(() => {
    const needle = query.trim().toLocaleLowerCase();
    return everyone.filter(
      (member) =>
        (party == null || member.party === party) &&
        (needle === "" ||
          member.name.toLocaleLowerCase().includes(needle) ||
          (member.place?.name.toLocaleLowerCase().includes(needle) ?? false)),
    );
  });
  const colors = $derived(
    new Map(
      ordered.map((entry) => [entry.key, swatch(entry.color, entry.key)]),
    ),
  );
  const labels = $derived(
    new Map(ordered.map((entry) => [entry.key, entry.short ?? entry.label])),
  );
</script>

{#if everyone.length > 0}
  <section
    class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="{uid}-title"
  >
    <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
      <h2 id="{uid}-title" class="heading">
        {m.members_title({ count: count(everyone.length) })}
      </h2>
      <input
        class="rounded-sm border border-input bg-card px-2.5 text-sm block-8 inline-[min(100%,16rem)] placeholder:text-muted-foreground"
        type="search"
        placeholder={m.members_filter()}
        aria-label={m.members_filter()}
        bind:value={query}
      />
    </div>
    <div
      class="flex flex-wrap gap-1.5"
      role="group"
      aria-label={m.members_party()}
    >
      {#each ordered as entry (entry.key)}
        <button
          type="button"
          class={cn(
            "flex items-center gap-1.5 rounded-xs px-1.5 text-[0.8125rem] block-7 hover:bg-secondary",
            { "marked bg-secondary": party === entry.key },
          )}
          style:--party={colors.get(entry.key)}
          aria-pressed={party === entry.key}
          onclick={() => (party = party === entry.key ? null : entry.key)}
        >
          <span class="rounded-xs bg-(--party) block-2.5 inline-2.5"></span>
          <span class="font-semibold">{labels.get(entry.key)}</span>
          <span class="font-figures text-xs text-muted-foreground tabular-nums"
            >{entry.members.filter((member) => member.name != null)
              .length}</span
          >
        </button>
      {/each}
    </div>
    <ul
      class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,14rem),1fr))] overflow-hidden rounded-sm ring-1 ring-border"
    >
      {#each isOpen || query !== "" ? shown : shown.slice(0, 48) as member, index (index)}
        <li
          class="grid border-s-3 bg-card px-2.5 py-1.5 text-[0.8125rem]/snug shadow-[0_0_0_0.5px_var(--border)]"
          style:border-inline-start-color={colors.get(member.party)}
        >
          <span class="truncate font-semibold">{member.name}</span>
          <span
            class="flex items-baseline gap-2 text-muted-foreground min-inline-0"
          >
            {#if member.place != null}
              {#if member.place.path == null}
                <span class="truncate">{member.place.name}</span>
              {:else}
                <a
                  class="truncate hover:text-foreground hover:underline hover:underline-offset-4"
                  href={resolve("/[...path]", { path: member.place.path })}
                  >{member.place.name}</a
                >
              {/if}
            {/if}
            {#if member.position != null}
              <span class="shrink-0 font-figures text-[0.6875rem] tabular-nums"
                >{m.seats_position({ position: member.position })}</span
              >
            {/if}
          </span>
        </li>
      {/each}
    </ul>
    {#if shown.length === 0}
      <p class="text-sm text-muted-foreground">{m.members_none()}</p>
    {:else if !isOpen && query === "" && shown.length > 48}
      <button
        type="button"
        class="justify-self-start rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold block-7 hover:bg-secondary"
        onclick={() => (isOpen = true)}
        >{m.members_all({ count: count(shown.length) })}</button
      >
    {/if}
  </section>
{/if}
