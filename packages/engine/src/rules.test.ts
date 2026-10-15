import schema from "@ea/engine/rules.schema.json" with { type: "json" };
import { expect, it } from "vitest";
import { rulesJsonSchema } from "./rules.ts";

it("keeps rules.schema.json in step with the rules schema", () => {
  expect(schema).toEqual(rulesJsonSchema);
});
