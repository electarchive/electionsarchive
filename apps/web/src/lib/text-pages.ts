import { PROJECTS } from "#lib/projects.js";

export const TEXT_PAGES: Record<
  string,
  { published: string; modified: string }
> = {
  ...Object.fromEntries(
    PROJECTS.map((slug) => [
      `/${slug}`,
      { published: "2026-11-01", modified: "2026-11-03" },
    ]),
  ),
  "/agents": { published: "2026-10-30", modified: "2026-11-03" },
  "/changelog": { published: "2026-11-02", modified: "2026-11-03" },
  "/live/methodology": { published: "2026-11-01", modified: "2026-11-03" },
  "/sources": { published: "2026-11-03", modified: "2026-11-03" },
};
