import { replaceState } from "$app/navigation";
import { page } from "$app/state";
import { untrack } from "svelte";

export function keep(params: Record<string, string | null>) {
  const url = new URL(location.href);
  for (const [key, value] of Object.entries(params)) {
    if (value == null) {
      url.searchParams.delete(key);
    } else {
      url.searchParams.set(key, value);
    }
  }
  if (url.search !== location.search) {
    void replaceState(
      url,
      untrack(() => page.state),
    );
  }
}
