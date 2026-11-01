<script lang="ts">
  import type { ComponentProps } from "svelte";
  import { plural } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import type { PageData } from "./$types";
  import PlaceMap from "./map.svelte";

  let {
    maps,
    hexes = null,
    since,
    year,
    focus = $bindable(null),
  }: {
    maps: NonNullable<
      Extract<PageData, { kind: "contest" }>["crowned"]
    >["maps"];
    hexes?: ComponentProps<typeof PlaceMap>["hexes"];
    since: string | null;
    year: string;
    focus?: number | null;
  } = $props();

  let level = $state<string | null>(null);

  const shape = $derived(
    maps.find((entry) => entry.places.level === level) ?? maps[0],
  );
</script>

{#if shape != null}
  {#if shape.map.tiles != null}
    {#key shape.places.level}
      <PlaceMap
        places={shape.places}
        colors={shape.places.legend.map((item) => swatch(item.color, item.key))}
        tiles={shape.map.tiles}
        own={shape.map.own}
        frame={shape.map.frame}
        blank={shape.map.blank ?? null}
        outlying={shape.map.outlying ?? null}
        parted={shape.map.parted ?? null}
        approximate={shape.map.approximate}
        sources={shape.map.credits}
        hexes={shape === maps[0] ? hexes : null}
        noun={shape.places.levels[0]?.noun ?? ""}
        votes=""
        names={shape.places.legend.map((item) => item.short ?? item.label)}
        levels={maps.map((entry) => {
          const name = plural(entry.places.levels[0]?.noun ?? "");
          return {
            code: entry.places.level,
            label: name.charAt(0).toUpperCase() + name.slice(1),
            count: entry.places.rows.length,
            path: "",
            query: "",
            isCurrent: entry === shape,
          };
        })}
        pick={(code) => {
          level = code;
        }}
        span={since == null ? null : { from: since, to: year, forward: false }}
        bind:focus
      />
    {/key}
  {:else}
    <p
      class="grid place-items-center border border-dashed border-border px-4 text-center text-sm text-pretty text-muted-foreground block-[min(24rem,50svh)]"
    >
      {m.map_rebuilding()}
    </p>
  {/if}
{/if}
