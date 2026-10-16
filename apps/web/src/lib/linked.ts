import { replaceState } from "$app/navigation";
import { page } from "$app/state";

export function link(params: Record<string, string | null>) {
  const url = new URL(page.url.href);
  for (const [key, value] of Object.entries(params)) {
    if (value == null) {
      url.searchParams.delete(key);
    } else {
      url.searchParams.set(key, value);
    }
  }
  if (url.search !== page.url.search) {
    void replaceState(url, page.state);
  }
}
