import type { PageLoad } from "./$types";

export const load: PageLoad = async ({ data }) => ({
  ...data,
  Gaps: (await import("#routes/coverage/gaps.svelte")).default,
});
