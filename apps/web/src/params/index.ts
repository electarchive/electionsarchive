import { defineParams } from "@sveltejs/kit/params";

const directions = ["indelible", "tally", "chamber"] as const;

export const params = defineParams({
  direction: (param) => directions.find((direction) => direction === param),
});
