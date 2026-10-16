<script lang="ts">
  import { resolve } from "$app/paths";
  import { createQuery } from "@tanstack/svelte-query";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const {
    id,
    chamber,
  }: {
    id: string;
    chamber: { size: number; seats: Record<string, number> } | null;
  } = $props();

  const uid = $props.id();

  const governments = createQuery(() =>
    orpc.elections.governments.queryOptions({ input: { id } }),
  );

  const cards = $derived(
    (governments.data ?? []).map((government) => {
      const parties = government.parties.map((party) => ({
        ...party,
        seats:
          party.seats ??
          (government.isBefore ? null : (chamber?.seats[party.id] ?? null)),
        tint: swatch(party.color, `party:${party.id}`),
      }));
      let seats = 0;
      for (const party of parties) {
        seats += party.seats ?? 0;
      }
      return {
        ...government,
        seats,
        size: government.isBefore ? null : (chamber?.size ?? null),
        parties,
        bar: parties
          .filter((party) => (party.seats ?? 0) > 0)
          .toSorted(
            (a, b) =>
              Number(a.role === "support") - Number(b.role === "support") ||
              (a.lean ?? Infinity) - (b.lean ?? Infinity),
          ),
      };
    }),
  );
</script>

{#if cards.some((card) => !card.isBefore)}
  <section
    class="col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3.5"
    aria-labelledby="{uid}-title"
  >
    <div class="grid gap-0.5">
      <h2
        id="{uid}-title"
        class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
      >
        {m.governments_title()}
      </h2>
      <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
        {m.governments_lede()}
      </p>
    </div>
    <ol
      class="grid auto-cols-[minmax(16rem,1fr)] grid-flow-col gap-3 overflow-x-auto pbe-1 max-sm:auto-cols-fr max-sm:grid-flow-row"
    >
      {#each cards as card (card.from + card.name)}
        <li
          class={cn(
            "grid content-start gap-2 rounded-sm border border-border p-3",
            {
              "border-dashed bg-muted/40": card.isBefore,
              "border-foreground/30": !card.isBefore,
            },
          )}
        >
          <span class="text-xs text-muted-foreground"
            >{card.isBefore
              ? m.governments_before()
              : m.governments_formed()}</span
          >
          <span class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span
              class="font-serif text-lg/tight font-[640] tracking-[-0.005em]"
              >{card.name}</span
            >
            {#if card.isCaretaker}
              <span class="highlighter px-1 text-xs font-semibold"
                >{m.governments_caretaker()}</span
              >
            {/if}
          </span>
          <span class="grid gap-0.5 text-[0.8125rem]">
            {#if card.head != null}
              <span
                >{m.governments_head({ name: "" })}<a
                  class="font-semibold hover:highlighter"
                  href={resolve("/id/[id]", { id: card.head.id })}
                  >{card.head.name}</a
                ></span
              >
            {/if}
            <span class="font-mono text-xs text-muted-foreground tabular-nums"
              >{date(card.from)} – {card.to == null
                ? m.governments_present()
                : date(card.to)}</span
            >
          </span>
          {#if card.seats > 0}
            <span
              class="relative flex gap-px overflow-hidden rounded-xs bg-muted block-2"
              aria-hidden="true"
            >
              {#each card.bar as party (party.id)}
                <span
                  class={cn({ "opacity-45": party.role === "support" })}
                  style:inline-size="{((party.seats ?? 0) /
                    (card.size ?? card.seats)) *
                    100}%"
                  style:background={party.tint}
                ></span>
              {/each}
              {#if card.size != null}
                <span
                  class="absolute inset-y-0 bg-foreground inline-px"
                  style:inset-inline-start="{((Math.floor(card.size / 2) + 1) /
                    card.size) *
                    100}%"
                ></span>
              {/if}
            </span>
          {/if}
          <ul class="grid gap-1 text-[0.8125rem]">
            {#each card.parties as party (party.id)}
              <li
                class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2"
              >
                <span
                  class={cn("rounded-xs block-2 inline-2", {
                    "opacity-45": party.role === "support",
                  })}
                  style:background={party.tint}
                ></span>
                <span class="truncate" title={party.label}
                  >{party.short ??
                    party.label}{#if party.role === "support"}<span
                      class="ms-1.5 text-xs text-muted-foreground"
                      >{m.governments_support()}</span
                    >{/if}</span
                >
                <span class="font-mono text-xs tabular-nums"
                  >{party.seats == null ? "" : count(party.seats)}</span
                >
              </li>
            {/each}
          </ul>
          {#if card.seats > 0}
            <span class="font-mono text-xs text-muted-foreground"
              >{card.size == null
                ? m.governments_seats({ seats: count(card.seats) })
                : m.polity_seats({
                    seats: count(card.seats),
                    size: count(card.size),
                  })}</span
            >
          {/if}
        </li>
      {/each}
    </ol>
  </section>
{/if}
