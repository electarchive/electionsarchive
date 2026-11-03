import type { PageLoad } from "./$types";

export const load: PageLoad = async ({ data }) => {
  const { template } = data.workspace;
  const pick = async <T>(
    isWanted: boolean,
    module: () => Promise<{ default: T }>,
  ) => (isWanted ? (await module()).default : null);
  const isTable = ![
    "election",
    "results",
    "merge",
    "lineage",
    "translate",
    "boundaries",
  ].includes(template);
  const [Election, Results, Merge, Lineage, Translate, Boundaries, Table] =
    await Promise.all([
      pick(template === "election", async () => import("./election.svelte")),
      pick(template === "results", async () => import("./results.svelte")),
      pick(template === "merge", async () => import("./merge.svelte")),
      pick(template === "lineage", async () => import("./lineage.svelte")),
      pick(template === "translate", async () => import("./translate.svelte")),
      pick(
        template === "boundaries",
        async () => import("./boundaries.svelte"),
      ),
      pick(isTable, async () => import("./table.svelte")),
    ]);
  return {
    ...data,
    Election,
    Results,
    Merge,
    Lineage,
    Translate,
    Boundaries,
    Table,
  };
};
