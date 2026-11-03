<script lang="ts">
  import { resolve } from "$app/paths";
  import MapIcon from "@lucide/svelte/icons/map";
  import { Button } from "#lib/components/ui/button/index.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    election,
    state,
    race = null,
    county = null,
  }: {
    election: string;
    state: string;
    race?: string | null;
    county?: string | null;
  } = $props();

  const href = $derived.by(() => {
    const path = `us/${state.toLowerCase()}/${election.split("/").at(-1) ?? ""}`;
    const query = new URLSearchParams();
    if (race != null) {
      query.set("race", race.split("/").at(-1) ?? race);
    }
    if (county != null) {
      query.set("county", county);
    }
    const search = query.toString();
    return `${resolve("/live/map/[...path]", { path })}${search === "" ? "" : `?${search}`}`;
  });
</script>

<Button
  {href}
  size="sm"
  variant="outline"
  title={m.livemap_open_label()}
  aria-label={m.livemap_open_label()}
>
  <MapIcon />{m.livemap_open()}
</Button>
