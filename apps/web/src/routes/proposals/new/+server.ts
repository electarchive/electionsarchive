import { error } from "@sveltejs/kit";
import * as v from "valibot";
import {
  graphemes,
  hex,
  isDay,
  languages,
  md5Pattern,
  noteKinds,
  noteLimit,
  origins,
  schemePattern,
  schemes,
  sourceKinds,
} from "#lib/contrib.js";
import { editable, propose } from "#lib/server/contrib.js";
import type { RequestHandler } from "./$types";

const Uuid = v.pipe(v.string(), v.uuid());

const Line = (max: number) =>
  v.pipe(
    v.string(),
    v.normalize("NFC"),
    v.trim(),
    v.minLength(1),
    v.maxLength(max),
  );

const Body = v.pipe(
  v.string(),
  v.normalize("NFC"),
  v.trim(),
  v.minLength(1),
  v.check((body) => graphemes(body) <= noteLimit),
);

const Hex = v.pipe(v.string(), v.trim(), v.regex(hex), v.toLowerCase());

const Day = v.pipe(v.string(), v.check(isDay));

const Reference = <Kind extends "id_add" | "id_drop">(kind: Kind) =>
  v.pipe(
    v.object({
      kind: v.literal(kind),
      scheme: v.picklist(schemes),
      value: v.pipe(v.string(), v.trim()),
    }),
    v.check(({ scheme, value }) => schemePattern[scheme].test(value)),
  );

const Change = v.variant("kind", [
  v.object({ kind: v.literal("name"), id: Uuid, text: Line(500) }),
  v.object({
    kind: v.literal("note_add"),
    noteKind: v.picklist(noteKinds),
    lang: v.picklist(languages),
    body: Body,
  }),
  v.object({ kind: v.literal("note_set"), id: Uuid, body: Body }),
  v.object({ kind: v.literal("note_drop"), id: Uuid }),
  v.object({
    kind: v.literal("translate"),
    note: Uuid,
    md5: v.pipe(v.string(), v.regex(md5Pattern)),
    lang: v.picklist(languages),
    origin: v.picklist(origins),
    body: v.pipe(
      v.string(),
      v.normalize("NFC"),
      v.trim(),
      v.minLength(1),
      v.maxLength(400_000),
    ),
  }),
  Reference("id_add"),
  Reference("id_drop"),
  v.object({ kind: v.literal("color_set"), from: v.nullable(Day), color: Hex }),
  v.object({ kind: v.literal("color_end"), from: v.nullable(Day), to: Day }),
  v.pipe(
    v.object({
      kind: v.literal("color_add"),
      color: Hex,
      from: v.nullable(Day),
      to: v.nullable(Day),
    }),
    v.check(({ from, to }) => from == null || to == null || from < to),
  ),
]);

const Fraction = v.pipe(v.number(), v.minValue(0), v.maxValue(1));

const Proposal = v.object({
  entityId: Uuid,
  changes: v.pipe(v.array(Change), v.minLength(1), v.maxLength(50)),
  evidence: v.nullable(
    v.variant("type", [
      v.object({ type: v.literal("source"), sourceId: Uuid }),
      v.object({
        type: v.literal("url"),
        url: v.pipe(
          v.string(),
          v.trim(),
          v.maxLength(2048),
          v.url(),
          v.regex(/^https?:\/\//i),
        ),
        title: Line(500),
        publisher: Line(200),
        kind: v.picklist(sourceKinds),
      }),
      v.object({
        type: v.literal("upload"),
        uploadId: Uuid,
        page: v.nullable(v.pipe(v.number(), v.integer(), v.minValue(1))),
        region: v.nullable(
          v.pipe(
            v.tuple([Fraction, Fraction, Fraction, Fraction]),
            v.check(([x0, y0, x1, y1]) => x0 < x1 && y0 < y1),
          ),
        ),
        title: Line(500),
        publisher: Line(200),
        kind: v.picklist(sourceKinds),
      }),
    ]),
  ),
  quote: v.pipe(v.string(), v.normalize("NFC"), v.trim(), v.maxLength(1000)),
  summary: Line(200),
});

export const GET: RequestHandler = async ({ url, setHeaders }) => {
  const entity = v.safeParse(Uuid, url.searchParams.get("entity"));
  if (!entity.success) {
    error(400, "entity must be a UUID");
  }
  setHeaders({ "cache-control": "no-store" });
  return Response.json(await editable(entity.output));
};

export const POST: RequestHandler = async ({ request, locals }) => {
  if (locals.user == null) {
    error(401, "Sign in to propose a change.");
  }
  const body = v.safeParse(Proposal, await request.json());
  if (!body.success) {
    error(400, body.issues[0].message);
  }
  const number = await propose({ userId: locals.user.id, ...body.output });
  return Response.json({ number }, { status: 201 });
};
