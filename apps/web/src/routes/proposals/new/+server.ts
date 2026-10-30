import { error } from "@sveltejs/kit";
import * as v from "valibot";
import { sourceKinds } from "#lib/contrib.js";
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

const Proposal = v.object({
  entityId: Uuid,
  nameId: Uuid,
  text: Line(500),
  evidence: v.variant("type", [
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
  ]),
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
