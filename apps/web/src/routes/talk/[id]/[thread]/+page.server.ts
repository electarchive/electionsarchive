import { error, fail, redirect } from "@sveltejs/kit";
import * as v from "valibot";
import { roles } from "#lib/server/contrib.js";
import {
  attempt,
  commenterOf,
  editAction,
  field,
  hideAction,
  Id,
} from "#lib/server/discussion-actions.js";
import { Body, threadComments, withTrees } from "#lib/server/discussion.js";
import {
  commentOnTopic,
  PublicId,
  setTopicStatus,
  talkEntity,
  topic,
} from "#lib/server/talk.js";
import type { Actions, PageServerLoad } from "./$types";

const located = async (id: string, thread: string) => {
  const parsed = v.safeParse(PublicId, id);
  if (!parsed.success || !v.is(Id, thread)) {
    error(404);
  }
  const entity = await talkEntity(parsed.output);
  return { entity, found: await topic(entity.id, thread) };
};

export const load: PageServerLoad = async ({ params, locals, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  const { entity, found } = await located(params.id, params.thread);
  if (entity.public_id !== params.id) {
    redirect(301, `/talk/${entity.public_id}/${found.id}`);
  }
  const viewer = locals.user?.id ?? null;
  const [comments, allowed] = await Promise.all([
    threadComments(found.id, viewer),
    roles(viewer ?? undefined),
  ]);
  const canModerate = allowed.admin || allowed.reviewer;
  const { author_user_id: author, ...rest } = found;
  return {
    entity,
    topic: rest,
    comments: withTrees(comments),
    commenter: commenterOf(locals.user),
    canModerate,
    canResolve: viewer != null && (viewer === author || canModerate),
  };
};

const self = (userId: string) => ({
  userId,
  keyId: null,
  agentName: null,
  agentModel: null,
});

export const actions: Actions = {
  comment: async ({ params, locals, request }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to comment." });
    }
    const userId = locals.user.id;
    const form = await request.formData();
    const body = v.safeParse(Body, form.get("body") ?? "");
    if (!body.success) {
      return fail(400, { message: body.issues[0].message });
    }
    const parent = field(form, "parent");
    if (parent != null && !v.is(Id, parent)) {
      return fail(400, { message: "That reply target isn't valid." });
    }
    const { entity, found } = await located(params.id, params.thread);
    return (
      (await attempt(async () =>
        commentOnTopic({
          author: self(userId),
          entityId: entity.id,
          threadId: found.id,
          body: body.output,
          parentId: parent,
        }),
      )) ?? { commented: true }
    );
  },
  status: async ({ params, locals, request }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to change a topic's status." });
    }
    const userId = locals.user.id;
    const form = await request.formData();
    const status = form.get("status");
    if (status !== "open" && status !== "resolved") {
      return fail(400, { message: "That status isn't valid." });
    }
    const { entity, found } = await located(params.id, params.thread);
    return (
      (await attempt(async () =>
        setTopicStatus({
          author: self(userId),
          entityId: entity.id,
          threadId: found.id,
          status,
        }),
      )) ?? { status }
    );
  },
  edit_comment: editAction,
  hide_comment: hideAction,
};
