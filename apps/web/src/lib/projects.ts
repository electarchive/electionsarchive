export const PROJECTS = [
  "about",
  "architecture",
  "indices",
  "license",
  "contribute",
  "privacy",
  "terms",
] as const;

export type Project = (typeof PROJECTS)[number];
