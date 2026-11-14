import { defineParams } from "@sveltejs/kit/params";
import { PROJECTS } from "../lib/projects.ts";

const directions = ["indelible", "tally", "chamber"] as const;
const CALVER = /^\d{4}\.\d{2}(?:\.\d{2})?$/u;
const TWIN = /^\d{4}\.\d{2}(?:\.\d{2})?\.(?:md|json)$/u;

export const params = defineParams({
  direction: (param) => directions.find((direction) => direction === param),
  project: (param) => PROJECTS.find((project) => project === param),
  calver: (param) => (CALVER.test(param) ? param : undefined),
  twin: (param) => (TWIN.test(param) ? param : undefined),
});
