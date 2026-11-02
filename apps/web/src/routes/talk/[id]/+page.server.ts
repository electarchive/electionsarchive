import { error, fail, isHttpError, redirect } from "@sveltejs/kit";
import * as v from "valibot";
import { commenterOf } from "#lib/server/discussion-actions.js";
import { Body } from "#lib/server/discussion.js";
import {
  PublicId,
  startTopic,
  talkEntity,
  Title,
  topics,
} from "#lib/server/talk.js";
import type { Actions, PageServerLoad } from "./$types";

const entityOf = async (id: string) => {
  const parsed = v.safeParse(PublicId, id);
  if (!parsed.success) {
    error(404);
  }
  return talkEntity(parsed.output);
};

export const load: PageServerLoad = async ({ params, locals, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  const entity = await entityOf(params.id);
  if (entity.public_id !== params.id) {
    redirect(301, `/talk/${entity.public_id}`);
  }
  return {
    entity,
    ...(await topics(entity.id)),
    commenter: commenterOf(locals.user),
  };
};

export const actions: Actions = {
  comment: async ({ params, locals, request }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to open a topic." });
    }
    const userId = locals.user.id;
    const form = await request.formData();
    const title = v.safeParse(Title, form.get("title") ?? "");
    if (!title.success) {
      return fail(400, { message: title.issues[0].message });
    }
    const body = v.safeParse(Body, form.get("body") ?? "");
    if (!body.success) {
      return fail(400, { message: body.issues[0].message });
    }
    const entity = await entityOf(params.id);
    let thread: string;
    try {
      ({ thread } = await startTopic({
        author: { userId, keyId: null, agentName: null, agentModel: null },
        entityId: entity.id,
        title: title.output,
        body: body.output,
      }));
    } catch (error_) {
      if (isHttpError(error_)) {
        return fail(error_.status, { message: error_.body.message });
      }
      throw error_;
    }
    redirect(303, `/talk/${entity.public_id}/${thread}`);
  },
};
