import { toJsonSchema } from "@valibot/to-json-schema";
import * as v from "valibot";

const publicId = (prefix: string) =>
  v.pipe(
    v.string(),
    v.regex(
      new RegExp(`^${prefix}_[0-7][0-9a-hjkmnp-tv-z]{25}$`),
      `Expected a ${prefix}_ public ID`,
    ),
  );

const share = v.pipe(v.number(), v.minValue(0), v.maxValue(1));

export const viewSpec = v.object({
  version: v.literal(1),
  title: v.optional(v.pipe(v.string(), v.maxLength(200))),
  data: v.object({
    contests: v.pipe(v.array(publicId("ct")), v.minLength(1)),
    resultSets: v.optional(v.array(publicId("rs"))),
    level: v.optional(v.string()),
    measure: v.optional(
      v.picklist([
        "votes",
        "seats",
        "vote_share",
        "turnout",
        "registered",
        "ballots_cast",
      ]),
      "votes",
    ),
    channel: v.optional(
      v.picklist(["all", "election_day", "early", "postal", "abroad"]),
      "all",
    ),
    compare: v.optional(
      v.union([
        v.object({ contests: v.pipe(v.array(publicId("ct")), v.minLength(1)) }),
        v.object({ previous: v.literal(true) }),
      ]),
    ),
  }),
  filters: v.optional(
    v.array(
      v.variant("field", [
        v.object({ field: v.literal("area"), in: v.array(publicId("ar")) }),
        v.object({ field: v.literal("option"), in: v.array(publicId("op")) }),
        v.object({ field: v.literal("party"), in: v.array(publicId("pt")) }),
        v.object({
          field: v.picklist(["margin", "turnout", "share"]),
          min: v.optional(share),
          max: v.optional(share),
        }),
      ]),
    ),
    [],
  ),
  panels: v.pipe(
    v.array(
      v.intersect([
        v.variant("kind", [
          v.object({
            kind: v.literal("map"),
            color: v.optional(
              v.picklist(["winner", "share", "swing", "turnout", "flags"]),
              "winner",
            ),
            option: v.optional(publicId("op")),
          }),
          v.object({
            kind: v.literal("chart"),
            chart: v.picklist([
              "bar",
              "hemicycle",
              "waffle",
              "swingometer",
              "scatter",
              "ternary",
              "sankey",
            ]),
          }),
          v.object({ kind: v.literal("table") }),
        ]),
        v.object({
          area: v.optional(
            v.object({
              x: v.pipe(v.number(), v.integer(), v.minValue(0)),
              y: v.pipe(v.number(), v.integer(), v.minValue(0)),
              w: v.pipe(v.number(), v.integer(), v.minValue(1)),
              h: v.pipe(v.number(), v.integer(), v.minValue(1)),
            }),
          ),
        }),
      ]),
    ),
    v.minLength(1),
  ),
});

export type ViewSpec = v.InferOutput<typeof viewSpec>;

export const viewSpecJsonSchema = toJsonSchema(viewSpec);

const ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz";

export function encodeId(prefix: string, uuid: string) {
  let value = BigInt(`0x${uuid.replaceAll("-", "")}`);
  let suffix = "";
  for (let index = 0; index < 26; index += 1) {
    suffix = `${ALPHABET[Number(value & 31n)] ?? ""}${suffix}`;
    value >>= 5n;
  }
  return `${prefix}_${suffix}`;
}

export function decodeId(id: string) {
  let value = 0n;
  const digits = id.slice(id.indexOf("_") + 1);
  for (const character of digits) {
    value = (value << 5n) | BigInt(ALPHABET.indexOf(character));
  }
  const hex = value.toString(16).padStart(32, "0");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
