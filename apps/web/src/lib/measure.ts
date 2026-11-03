import type { LiveRule } from "@ea/analytics/live";
import * as v from "valibot";
import { percent } from "#lib/format.js";
import { m } from "#lib/paraglide/messages.js";

const Rules = v.object({
  formula: v.literal("referendum"),
  majority: v.optional(
    v.object({ share: v.number(), strict: v.optional(v.boolean(), false) }),
  ),
  minimum: v.optional(
    v.object({
      share: v.number(),
      strict: v.optional(v.boolean(), false),
      of: v.picklist(["ballots", "governor", "previous_general"]),
    }),
  ),
});

export const Rule = v.object({
  majority: v.nullable(v.object({ share: v.number(), strict: v.boolean() })),
  minimum: v.nullable(
    v.object({
      share: v.number(),
      strict: v.boolean(),
      of: v.picklist(["ballots", "governor", "previous_general"]),
    }),
  ),
  base: v.optional(v.nullable(v.number())),
});

export function rule(rules: unknown): LiveRule | null {
  const parsed = v.safeParse(Rules, rules);
  if (
    !parsed.success ||
    (parsed.output.majority == null && parsed.output.minimum == null)
  ) {
    return null;
  }
  return {
    majority: parsed.output.majority ?? null,
    minimum: parsed.output.minimum ?? null,
  };
}

export function shown(share: number) {
  return percent(
    share,
    Number.isSafeInteger(Math.round(share * 1e6) / 1e4) ? 0 : 1,
  );
}

export function line(found: LiveRule | null | undefined) {
  return found?.majority?.share ?? 0.5;
}

export function clears(
  found: LiveRule | null | undefined,
  yes: number,
  no: number,
) {
  const total = yes + no;
  const majority = found?.majority;
  const isPopular =
    majority == null
      ? yes > no
      : majority.strict
        ? yes > majority.share * total
        : yes >= majority.share * total;
  const minimum = found?.minimum;
  if (minimum == null || minimum.of === "previous_general") {
    return isPopular;
  }
  const bar =
    minimum.share *
    (minimum.of === "ballots"
      ? Math.max(found?.base ?? 0, total)
      : (found?.base ?? 0));
  return isPopular && (minimum.strict ? yes > bar : yes >= bar);
}

export function terms(found: LiveRule | null | undefined) {
  if (found == null) {
    return [];
  }
  const said: Array<string> = [];
  const majority = found.majority;
  if (majority != null && majority.share > 0.5) {
    said.push(
      Math.abs(majority.share - 2 / 3) < 0.001
        ? m.measure_rule_two_thirds()
        : m.measure_rule_majority({ share: shown(majority.share) }),
    );
  }
  const minimum = found.minimum;
  if (minimum?.of === "ballots") {
    said.push(
      minimum.strict
        ? m.measure_rule_ballots_over({ share: shown(minimum.share) })
        : m.measure_rule_ballots_least({ share: shown(minimum.share) }),
    );
  } else if (minimum?.of === "governor") {
    said.push(m.measure_rule_governor({ share: shown(minimum.share) }));
  } else if (minimum?.of === "previous_general") {
    said.push(m.measure_rule_previous());
  }
  return said;
}
