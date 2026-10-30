<script lang="ts">
  import { untrack } from "svelte";
  import type { DiffRow, Drawn } from "#lib/contrib.js";
  import type { Box } from "#lib/map/engine.svelte.js";
  import BoundaryKey from "#lib/components/contrib/boundary-key.svelte";
  import { extent } from "#lib/contrib.js";
  import { count } from "#lib/format.js";
  import { sketch } from "#lib/map/boundaries.js";
  import { Engine } from "#lib/map/engine.svelte.js";
  import { m } from "#lib/paraglide/messages.js";

  const { drawn, rows }: { drawn: Drawn; rows: Array<DiffRow> } = $props();

  const WORLD: Box = { west: -170, south: -58, east: 170, north: 75 };

  const boxes = $derived.by(() => {
    const found: Record<string, Box> = {};
    for (const feature of drawn.features) {
      const { area } = feature.properties;
      if (feature.properties.kind === "after" && area != null) {
        const box = extent(feature.geometry, found[area] ?? null);
        if (box != null) {
          found[area] = box;
        }
      }
    }
    return found;
  });
  const frame = $derived.by(() => {
    let box: Box | null = null;
    for (const feature of drawn.features) {
      box = extent(feature.geometry, box);
    }
    return box ?? WORLD;
  });
  const labels = $derived(
    new Map(
      drawn.features
        .filter((feature) => feature.properties.kind === "after")
        .map((feature) => [
          feature.properties.area ?? "",
          {
            code: feature.properties.code ?? "",
            name: feature.properties.label ?? "",
          },
        ]),
    ),
  );
  const kinds = $derived(
    new Set(drawn.features.map((feature) => feature.properties.kind)),
  );

  const engine = new Engine(
    untrack(() => frame),
    (width, height) => {
      const edge = Math.max(16, Math.round(Math.min(width, height) * 0.05));
      return { top: edge, right: edge, bottom: edge, left: edge };
    },
    { cooperative: true, onReady: () => untrack(draw) },
  );

  $effect(() => {
    void drawn;
    if (engine.gl != null) {
      draw();
    }
  });

  function draw() {
    sketch(engine, drawn, "proposal-bd", true);
    engine.frame(frame);
  }
</script>

<div
  class="relative overflow-hidden rounded-sm bg-card ring-1 ring-border block-[min(28rem,55svh)]"
>
  <div class="block-full inline-full" {@attach engine.mount}></div>
</div>
<div class="mbs-2">
  <BoundaryKey
    kinds={[
      "current",
      "upload",
      ...(drawn.features.some(
        (feature) =>
          feature.properties.kind === "after" &&
          feature.properties.status === "new",
      )
        ? ["new" as const]
        : []),
      ...(["moved", "gap", "overlap", "parent"] as const).filter((kind) =>
        kinds.has(kind),
      ),
    ]}
  />
</div>
<table class="mbs-3 text-sm inline-full">
  <thead class="text-xs text-muted-foreground">
    <tr class="border-be">
      <th class="py-1 text-start font-normal">{m.ws_bd_diff_area()}</th>
      <th class="py-1 text-end font-normal">{m.ws_bd_diff_km2()}</th>
    </tr>
  </thead>
  <tbody>
    {#each rows as row (`${row.key.area_id}|${row.key.valid_from}`)}
      {const named = $derived(labels.get(row.key.area_id ?? ""))}
      {const area = $derived(row.fields.area_km2)}
      {const box = $derived(boxes[row.key.area_id ?? ""])}
      <tr class="border-be">
        <td class="py-1.5 pe-2">
          <button
            type="button"
            class="text-start hover:underline disabled:no-underline"
            disabled={box == null}
            onclick={() => {
              if (box != null) {
                engine.fit(box);
              }
            }}
          >
            <span class="font-figures font-semibold">{named?.code}</span>
            <span class="ms-1">{named?.name}</span>
          </button>
          {#if row.op === "add"}
            <span class="ms-1 text-xs font-semibold"
              >{m.ws_bd_status_new()}</span
            >
          {/if}
        </td>
        <td class="py-1.5 text-end font-figures whitespace-nowrap">
          {#if area != null}
            {#if area[0] != null && area[0] !== area[1]}<span
                class="text-muted-foreground">{count(Number(area[0]))} →</span
              >{/if}
            {count(Number(area[1]))}
          {/if}
        </td>
      </tr>
    {/each}
  </tbody>
</table>
