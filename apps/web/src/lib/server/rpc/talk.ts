import { openapi } from "@orpc/openapi";
import { error } from "@sveltejs/kit";
import * as v from "valibot";
import {
  AnchorSchema,
  Body,
  commentOnProposal,
  SuggestionSchema,
  threadComments,
} from "#lib/server/discussion.js";
import { Number_, reader, run, writer } from "#lib/server/rpc/keyed.js";
import {
  commentOnTopic,
  PublicId,
  setTopicStatus,
  startTopic,
  talkEntity,
  Title,
  topic,
  topics,
} from "#lib/server/talk.js";

const Uuid = v.pipe(v.string(), v.uuid());

async function read(id: string, thread: string, viewer: string | null) {
  const entity = await talkEntity(id);
  const { author_user_id: _author, ...found } = await topic(entity.id, thread);
  const comments = await threadComments(found.id, viewer);
  return {
    entity,
    topic: found,
    comments: comments.map(({ mine: _mine, ...comment }) => comment),
  };
}

export const talk = {
  list: reader
    .meta(
      openapi({
        method: "GET",
        path: "/talk/{id}",
        summary:
          "A record's talk page by its public ID: its topics (open first, then resolved, each with its comment count) and the submitted proposals that touch it",
      }),
    )
    .input(v.object({ id: PublicId }))
    .handler(async ({ input }) =>
      run(async () => {
        const entity = await talkEntity(input.id);
        return { entity, ...(await topics(entity.id)) };
      }),
    ),
  topic: reader
    .meta(
      openapi({
        method: "GET",
        path: "/talk/{id}/{thread}",
        summary:
          "One talk topic and its comments, oldest first, with replies by parent_id; hidden comments keep their reason and lose their body",
      }),
    )
    .input(v.object({ id: PublicId, thread: Uuid }))
    .handler(async ({ input, context }) =>
      run(async () =>
        read(input.id, input.thread, context.owner?.userId ?? null),
      ),
    ),
  open: writer
    .meta(
      openapi({
        method: "POST",
        path: "/talk/{id}",
        summary:
          "Open a topic on a record's talk page with a title (200 characters) and a first comment in Markdown (10,000 characters); @handle mentions notify",
      }),
    )
    .input(v.object({ id: PublicId, title: Title, body_md: Body }))
    .handler(async ({ input, context: { owner } }) =>
      run(async () => {
        const entity = await talkEntity(input.id);
        const created = await startTopic({
          author: owner,
          entityId: entity.id,
          title: input.title,
          body: input.body_md,
        });
        return read(input.id, created.thread, owner.userId);
      }),
    ),
  reply: writer
    .meta(
      openapi({
        method: "POST",
        path: "/talk/{id}/{thread}/comments",
        summary:
          "Comment on a talk topic in Markdown, optionally replying to a comment (parent); the topic's author and everyone in it are notified",
      }),
    )
    .input(
      v.object({
        id: PublicId,
        thread: Uuid,
        body_md: Body,
        parent: v.optional(Uuid),
      }),
    )
    .handler(async ({ input, context: { owner } }) =>
      run(async () => {
        const entity = await talkEntity(input.id);
        return {
          id: await commentOnTopic({
            author: owner,
            entityId: entity.id,
            threadId: input.thread,
            body: input.body_md,
            parentId: input.parent ?? null,
          }),
        };
      }),
    ),
  status: writer
    .meta(
      openapi({
        method: "POST",
        path: "/talk/{id}/{thread}/status",
        summary:
          "Resolve or reopen a talk topic; only its author or a reviewer can",
      }),
    )
    .input(
      v.object({
        id: PublicId,
        thread: Uuid,
        status: v.picklist(["open", "resolved"]),
      }),
    )
    .handler(async ({ input, context: { owner } }) =>
      run(async () => {
        const entity = await talkEntity(input.id);
        await setTopicStatus({
          author: owner,
          entityId: entity.id,
          threadId: input.thread,
          status: input.status,
        });
        return read(input.id, input.thread, owner.userId);
      }),
    ),
  comment: writer
    .meta(
      openapi({
        method: "POST",
        path: "/comments",
        summary:
          "Comment anywhere in one call. With proposal, it comments on that proposal (parent, anchor, and suggestion as on its comments endpoint). With entity (a public ID), it replies to the talk topic named by thread, or opens a new topic when you give a title instead",
      }),
    )
    .input(
      v.object({
        proposal: v.optional(Number_),
        entity: v.optional(PublicId),
        thread: v.optional(Uuid),
        title: v.optional(Title),
        body_md: Body,
        parent: v.optional(Uuid),
        anchor: v.optional(AnchorSchema),
        suggestion: v.optional(SuggestionSchema),
      }),
    )
    .handler(async ({ input, context: { owner } }) =>
      run(async () => {
        if (input.proposal != null && input.entity != null) {
          error(400, "Give either proposal or entity, not both.");
        }
        if (input.proposal != null) {
          return {
            id: await commentOnProposal({
              author: owner,
              number: Number(input.proposal),
              body: input.body_md,
              parentId: input.parent ?? null,
              anchor: input.anchor ?? null,
              suggestion: input.suggestion ?? null,
            }),
            thread: null,
          };
        }
        if (input.entity == null) {
          error(400, "Give a proposal number or an entity's public ID.");
        }
        const entity = await talkEntity(input.entity);
        if (input.thread != null) {
          return {
            id: await commentOnTopic({
              author: owner,
              entityId: entity.id,
              threadId: input.thread,
              body: input.body_md,
              parentId: input.parent ?? null,
            }),
            thread: input.thread,
          };
        }
        if (input.title == null) {
          error(400, "Give a thread to reply to, or a title to open a topic.");
        }
        const created = await startTopic({
          author: owner,
          entityId: entity.id,
          title: input.title,
          body: input.body_md,
        });
        return { id: created.comment, thread: created.thread };
      }),
    ),
};
