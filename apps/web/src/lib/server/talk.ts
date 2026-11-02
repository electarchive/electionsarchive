import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { TalkProposal, TopicView } from "#lib/contrib.js";
import type { Author } from "#lib/server/discussion.js";
import { mentionsOf } from "#lib/markdown.js";
import { roles } from "#lib/server/contrib.js";
import { db } from "#lib/server/db/index.js";
import { allowed } from "#lib/server/discussion.js";
import { names } from "#lib/server/rpc/archive.js";

const publicPattern = /^[a-z]{2}_[0-7][0-9a-hjkmnp-tv-z]{25}$/u;

export const PublicId = v.pipe(
  v.string(),
  v.trim(),
  v.toLowerCase(),
  v.check(
    (value) =>
      publicPattern.test(value) || v.is(v.pipe(v.string(), v.uuid()), value),
    "Give a public ID such as pt_46ye3p1ss3bqyrkaj53sqz5g8f, or a UUID.",
  ),
);

export const Title = v.pipe(
  v.string(),
  v.normalize("NFC"),
  v.trim(),
  v.minLength(1, "Give the topic a title."),
  v.maxLength(200, "A topic title is limited to 200 characters."),
);

export type TalkEntity = {
  id: string;
  public_id: string;
  type: string;
  name: string;
};

const polityOf = (entity: string) => sql`
  array_remove(array[(
    select case e.type
      when 'polity' then e.id
      when 'party' then (select polity_id from party where id = e.id)
      when 'election' then (select polity_id from election where id = e.id)
      when 'contest' then (select el.polity_id from contest c join election el on el.id = c.election_id where c.id = e.id)
    end
    from entity e where e.id = ${entity}::uuid
  )], null)
`;

export async function talkEntity(publicId: string): Promise<TalkEntity> {
  const { rows } = await db.execute<Omit<TalkEntity, "name">>(sql`
    with asked as (
      select id from entity
      where ${
        publicPattern.test(publicId)
          ? sql`id = ea_resolve(${publicId}) and ea_prefix(type) = split_part(${publicId}, '_', 1)`
          : sql`id = ${publicId}::uuid`
      }
    )
    select e.id, e.type::text as type, ea_public_id(e.type, e.id) as public_id
    from asked a
    join entity e on e.id = coalesce((select into_id from entity_merge m where m.entity_id = a.id), a.id)
    where e.retired_by is null
  `);
  const found = rows[0];
  if (found == null) {
    error(404, "No record has that public ID.");
  }
  const { full } = await names([found.id]);
  return { ...found, name: full.get(found.id) ?? found.public_id };
}

export async function talkLink(entityId: string) {
  const { rows } = await db.execute<{ id: string; open: number }>(sql`
    select ea_public_id(e.type, e.id) as id,
           (select count(*)::int from thread t
            where t.kind = 'talk' and t.entity_id = e.id and t.status = 'open') as open
    from entity e where e.id = ${entityId}::uuid
  `);
  return rows[0] ?? null;
}

const topicColumns = sql`
  t.id, t.title, t.status::text as status, u.name as author, p.handle,
  t.created_at::text, t.updated_at::text,
  (select count(*)::int from comment c where c.thread_id = t.id and c.hidden_at is null) as comments
`;

export async function topics(entityId: string) {
  const [found, touching] = await Promise.all([
    db.execute<TopicView>(sql`
      select ${topicColumns}
      from thread t
      left join users u on u.id = t.author_user_id
      left join profile p on p.user_id = t.author_user_id
      where t.kind = 'talk' and t.entity_id = ${entityId}::uuid
      order by t.status = 'resolved', t.updated_at desc
      limit 500
    `),
    db.execute<TalkProposal>(sql`
      select p.number, p.title, p.state::text as state, p.updated_at::text
      from proposal p
      join proposal_revision r on r.proposal_id = p.id and r.changeset_id = p.head_changeset_id
      where p.submitted_at is not null and r.diff -> 'entities' ? ${entityId}
      order by p.number desc
      limit 50
    `),
  ]);
  return { topics: found.rows, proposals: touching.rows };
}

export async function topic(entityId: string, threadId: string) {
  const { rows } = await db.execute<
    TopicView & { author_user_id: string | null }
  >(sql`
    select ${topicColumns}, t.author_user_id
    from thread t
    left join users u on u.id = t.author_user_id
    left join profile p on p.user_id = t.author_user_id
    where t.id = ${threadId}::uuid and t.kind = 'talk' and t.entity_id = ${entityId}::uuid
  `);
  const found = rows[0];
  if (found == null) {
    error(404, "This record has no topic with that ID.");
  }
  return found;
}

const mentioned = (body: string) => sql`
  array(select f.user_id from profile f where f.handle = any(${sql.param(mentionsOf(body))}::text[]))
`;

export async function startTopic({
  author,
  entityId,
  title,
  body,
}: {
  author: Author;
  entityId: string;
  title: string;
  body: string;
}) {
  await allowed(author.userId);
  return db.transaction(async (tx) => {
    const { rows } = await tx.execute<{ thread: string; comment: string }>(sql`
      with t as (
        insert into thread (kind, entity_id, title, author_user_id)
        values ('talk', ${entityId}::uuid, ${title}, ${author.userId})
        returning id
      )
      insert into comment (thread_id, author_user_id, agent_key_id, agent_name, agent_model, body_md)
      select t.id, ${author.userId}, ${author.keyId}, ${author.agentName}, ${author.agentModel}, ${body}
      from t
      returning thread_id as thread, id as comment
    `);
    const created = rows[0];
    if (created == null) {
      error(500);
    }
    await tx.execute(sql`
      insert into activity (actor_user_id, agent_key_id, kind, thread_id, comment_id,
                            entity_ids, polity_ids, notify_user_ids, summary)
      values (${author.userId}, ${author.keyId}, 'topic_opened', ${created.thread}, ${created.comment},
              array[${entityId}::uuid], ${polityOf(entityId)},
              array(select x from unnest(${mentioned(body)}) x where x <> ${author.userId}::uuid),
              format('Topic opened: %s', ${title}::text))
    `);
    return created;
  });
}

export async function commentOnTopic({
  author,
  entityId,
  threadId,
  body,
  parentId,
}: {
  author: Author;
  entityId: string;
  threadId: string;
  body: string;
  parentId: string | null;
}) {
  await allowed(author.userId);
  const found = await topic(entityId, threadId);
  return db.transaction(async (tx) => {
    const { rows } = await tx.execute<{ id: string }>(sql`
      with parent as (
        select coalesce(c.parent_id, c.id) as id from comment c
        where c.id = ${parentId}::uuid and c.thread_id = ${found.id}::uuid
      )
      insert into comment (thread_id, parent_id, author_user_id, agent_key_id, agent_name, agent_model, body_md)
      select ${found.id}::uuid, (select id from parent), ${author.userId}, ${author.keyId},
             ${author.agentName}, ${author.agentModel}, ${body}
      where ${parentId}::uuid is null or exists (select from parent)
      returning id
    `);
    const comment = rows[0];
    if (comment == null) {
      error(400, "The comment you're replying to isn't in this topic.");
    }
    await tx.execute(sql`
      update thread set updated_at = now() where id = ${found.id}::uuid
    `);
    await tx.execute(sql`
      insert into activity (actor_user_id, agent_key_id, kind, thread_id, comment_id,
                            entity_ids, polity_ids, notify_user_ids, summary)
      values (${author.userId}, ${author.keyId}, 'commented', ${found.id}::uuid, ${comment.id},
              array[${entityId}::uuid], ${polityOf(entityId)},
              array(
                select distinct x from unnest(
                  array_remove(array[${found.author_user_id}::uuid], null)
                  || array(select c.author_user_id from comment c
                           where c.thread_id = ${found.id}::uuid and c.author_user_id is not null)
                  || ${mentioned(body)}
                ) x
                where x <> ${author.userId}::uuid
              ),
              format('Comment on the topic %s', ${found.title}::text))
    `);
    return comment.id;
  });
}

export async function setTopicStatus({
  author,
  entityId,
  threadId,
  status,
}: {
  author: Author;
  entityId: string;
  threadId: string;
  status: "open" | "resolved";
}) {
  const found = await topic(entityId, threadId);
  const allowedRoles = await roles(author.userId);
  if (
    found.author_user_id !== author.userId &&
    !(allowedRoles.admin || allowedRoles.reviewer)
  ) {
    error(403, "Only the topic's author or a reviewer can change its status.");
  }
  if (found.status === status) {
    return;
  }
  await db.transaction(async (tx) => {
    await tx.execute(sql`
      update thread set status = ${status}::thread_status, updated_at = now()
      where id = ${found.id}::uuid
    `);
    if (status === "resolved") {
      await tx.execute(sql`
        insert into activity (actor_user_id, agent_key_id, kind, thread_id, entity_ids, polity_ids,
                              notify_user_ids, summary)
        values (${author.userId}, ${author.keyId}, 'topic_resolved', ${found.id}::uuid,
                array[${entityId}::uuid], ${polityOf(entityId)},
                array(
                  select distinct c.author_user_id from comment c
                  where c.thread_id = ${found.id}::uuid and c.author_user_id is not null
                    and c.author_user_id <> ${author.userId}::uuid
                ),
                format('Topic resolved: %s', ${found.title}::text))
      `);
    }
  });
}
