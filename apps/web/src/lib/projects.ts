export const PROJECTS = [
  "about",
  "architecture",
  "license",
  "contribute",
  "privacy",
  "terms",
] as const;

export type Project = (typeof PROJECTS)[number];
