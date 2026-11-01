import { defineParams } from "@sveltejs/kit/params";
import { PROJECTS } from "#lib/projects.js";

const directions = ["indelible", "tally", "chamber"] as const;

export const params = defineParams({
  direction: (param) => directions.find((direction) => direction === param),
  project: (param) => PROJECTS.find((project) => project === param),
});
