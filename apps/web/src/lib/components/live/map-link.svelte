<script lang="ts">
  import { resolve } from "$app/paths";
  import MapIcon from "@lucide/svelte/icons/map";
  import { Button } from "#lib/components/ui/button/index.js";
  import { m } from "#lib/paraglide/messages.js";

  const { election }: { election: string } = $props();
</script>

<Button
  href={resolve("/live/map/[...path]", { path: election })}
  size="sm"
  variant="outline"
  title={m.livemap_open_label()}
  aria-label={m.livemap_open_label()}
>
  <MapIcon />{m.livemap_open()}
</Button>
