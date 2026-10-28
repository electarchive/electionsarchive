import { resolve } from "$app/paths";
import { page } from "$app/state";

export function carried() {
  const round = page.url.searchParams.get("round");
  return round == null ? ("" as const) : (`?round=${round}` as const);
}

export function unit(path: string) {
  return resolve(`/[...path]${carried()}`, { path });
}
