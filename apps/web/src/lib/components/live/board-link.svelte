<script lang="ts">
  import { resolve } from "$app/paths";
  import LayoutDashboardIcon from "@lucide/svelte/icons/layout-dashboard";
  import { Button } from "#lib/components/ui/button/index.js";
  import { m } from "#lib/paraglide/messages.js";

  const { election, race = null }: { election: string; race?: string | null } =
    $props();

  const href = $derived(
    `${resolve("/live/board/[...path]", { path: election })}${race == null ? "" : `?race=${encodeURIComponent(race)}`}`,
  );
</script>

<Button
  {href}
  size="sm"
  variant="outline"
  class="ms-auto"
  title={m.board_open_label()}
  aria-label={m.board_open_label()}
>
  <LayoutDashboardIcon />{m.board_open()}
</Button>
