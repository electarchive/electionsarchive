import { error } from "@sveltejs/kit";
import * as v from "valibot";
import {
  checkDraft,
  saveDraft,
  submitDraft,
} from "#lib/server/rpc/proposals.js";
import { Draft, latest } from "#lib/server/workspace.js";
import type { RequestHandler } from "./$types";

const owner = (user: App.Locals["user"]) => {
  if (user == null) {
    error(401, "Sign in to edit a draft.");
  }
  return { userId: user.id, keyId: null, agentName: null, agentModel: null };
};

const parse = (value: string) => {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 1) {
    error(404);
  }
  return number;
};

export const PUT: RequestHandler = async ({ params, locals, request }) => {
  const by = owner(locals.user);
  const body = v.safeParse(Draft, await request.json());
  if (!body.success) {
    error(400, body.issues[0].message);
  }
  await saveDraft(parse(params.number), by, body.output);
  return new Response(null, { status: 204 });
};

export const POST: RequestHandler = async ({ params, locals, request }) => {
  const by = owner(locals.user);
  const body = v.safeParse(
    v.object({ do: v.picklist(["check", "submit"]) }),
    await request.json(),
  );
  if (!body.success) {
    error(400, body.issues[0].message);
  }
  const number = parse(params.number);
  await (body.output.do === "check" ? checkDraft : submitDraft)(number, by);
  return Response.json({ check: await latest(number, body.output.do) });
};
