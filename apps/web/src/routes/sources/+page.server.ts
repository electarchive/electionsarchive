import { listSources } from "#lib/server/sources.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async () => {
  const { total, categories } = await listSources();
  return {
    total,
    categories: categories.map((category) => ({
      ...category,
      sources: category.sources.map((source) => ({
        ...source,
        members: source.members.map(
          ({ name, url, contests, shapes, countries }) => ({
            name,
            url,
            contests,
            shapes,
            countries,
          }),
        ),
      })),
    })),
  };
};
