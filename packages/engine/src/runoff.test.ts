import { describe, expect, it } from "vitest";
import type { Rules } from "./rules.ts";
import indonesia from "./fixtures/indonesia-2024.json" with { type: "json" };
import { advance } from "./runoff.ts";

const spread: Extract<Rules, { formula: "two_round" }> = {
  formula: "two_round",
  majority: 0.5,
  advance: 2,
  spread: {
    scheme: "id.provinsi",
    share: 0.2,
    units: { share: 0.5, strict: true },
  },
};

describe("two-round spread", () => {
  it("elects Indonesia's 2024 ticket with a majority and 20 percent in more than half of the provinces", () => {
    expect(
      advance(spread, {
        entries: indonesia.entries,
        units: indonesia.provinces,
      }),
    ).toEqual({ elected: indonesia.elected });
  });
});
