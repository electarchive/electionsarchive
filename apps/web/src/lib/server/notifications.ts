import { BASE_URL } from "$app/env/public";
import { sql } from "drizzle-orm";
import type { FeedEntry } from "#lib/contrib.js";
import { byline, describe } from "#lib/contrib.js";
import { m } from "#lib/paraglide/messages.js";
import { authDb, db } from "#lib/server/db/index.js";
import { sendEmail } from "#lib/server/email.js";
import { stream, visible, watchedStream } from "#lib/server/feeds.js";

export const SHOWS = ["all", "direct", "watched"] as const;

export const CADENCES = ["off", "daily", "weekly"] as const;

export const LISTS = ["direct", "digest"] as const;

export type Cadence = (typeof CADENCES)[number];

export type List = (typeof LISTS)[number];

const DIGEST_EVENTS = 20;

const LATEST = 8;

const link = (path: string) => new URL(path, BASE_URL).href;

export async function unreadCount(userId: string) {
  const { rows } = await db.execute<{ count: number }>(sql`
    select count(*)::int as count from (
      select from notification n
      join activity a on a.id = n.activity_id
      left join proposal p on p.id = a.proposal_id
      left join comment c on c.id = a.comment_id
      where n.user_id = ${userId}::uuid and n.read_at is null and ${visible}
      limit 100
    ) x
  `);
  return rows[0]?.count ?? 0;
}

export async function inbox(
  userId: string,
  show: (typeof SHOWS)[number],
  before: string | null,
  limit?: number,
) {
  const found = await stream({
    activity: sql`
      a.id in (
        select n.activity_id from notification n
        where n.user_id = ${userId}::uuid and (${show}::text = 'all' or n.reason::text = ${show}::text)
      )
    `,
    imports: null,
    before,
    limit,
  });
  const ids = found.entries.map((entry) => entry.id);
  const { rows } =
    ids.length === 0
      ? { rows: [] }
      : await db.execute<{
          activity_id: string;
          reason: "direct" | "watched";
          unread: boolean;
        }>(sql`
          select activity_id::text, reason::text, read_at is null as unread from notification
          where user_id = ${userId}::uuid and activity_id = any(${sql.param(ids)}::uuid[])
        `);
  return {
    ...found,
    marks: Object.fromEntries(
      rows.map((row) => [
        row.activity_id,
        { reason: row.reason, unread: row.unread },
      ]),
    ),
  };
}

export async function latest(userId: string) {
  const [found, unread] = await Promise.all([
    inbox(userId, "all", null, LATEST),
    unreadCount(userId),
  ]);
  return {
    unread,
    entries: found.entries.map((entry) => {
      const described = describe(entry);
      const mark = found.marks[entry.id];
      return {
        id: entry.id,
        at: entry.at,
        text: described.text,
        href: described.href,
        byline: byline(entry),
        polity: entry.polity,
        watched: mark?.reason === "watched",
        unread: mark?.unread ?? false,
      };
    }),
  };
}

export type Latest = Awaited<ReturnType<typeof latest>>;

export async function markRead(userId: string, ids: Array<string> | null) {
  await db.execute(sql`
    update notification set read_at = now()
    where user_id = ${userId}::uuid and read_at is null
      and (${ids == null} or activity_id = any(${sql.param(ids ?? [])}::uuid[]))
  `);
}

export async function settings(userId: string) {
  const { rows } = await db.execute<{
    direct_email: boolean;
    digest: Cadence;
    unsubscribe_token: string;
  }>(sql`
    insert into notification_setting (user_id) values (${userId}::uuid)
    on conflict (user_id) do update set user_id = excluded.user_id
    returning direct_email, digest::text, unsubscribe_token
  `);
  const [row] = rows;
  if (row == null) {
    throw new Error(`No notification settings for ${userId}`);
  }
  return row;
}

export async function saveSettings(
  userId: string,
  { direct, digest }: { direct: boolean; digest: Cadence },
) {
  await db.execute(sql`
    insert into notification_setting (user_id, direct_email, digest, digest_through)
    values (${userId}::uuid, ${direct}, ${digest}::digest_cadence,
            case when ${digest}::text <> 'off' then now() end)
    on conflict (user_id) do update set
      direct_email = excluded.direct_email,
      digest = excluded.digest,
      digest_through = case
        when excluded.digest = 'off' then null
        when notification_setting.digest = 'off' then now()
        else notification_setting.digest_through end,
      updated_at = now()
  `);
}

const TOKEN = /^[0-9a-f]{64}$/u;

export async function subscription(token: string) {
  if (!TOKEN.test(token)) {
    return null;
  }
  const { rows } = await db.execute<{ direct_email: boolean; digest: Cadence }>(
    sql`
      select direct_email, digest::text from notification_setting
      where unsubscribe_token = ${token}
    `,
  );
  return rows[0] ?? null;
}

export async function unsubscribe(token: string, list: List) {
  if (!TOKEN.test(token)) {
    return false;
  }
  const { rows } = await db.execute<{ user_id: string }>(sql`
    update notification_setting set
      direct_email = direct_email and ${list}::text <> 'direct',
      digest = case when ${list}::text = 'digest' then 'off' else digest end,
      digest_through = case when ${list}::text = 'digest' then null else digest_through end,
      updated_at = now()
    where unsubscribe_token = ${token}
    returning user_id
  `);
  return rows.length > 0;
}

async function recipients(ids: Array<string>) {
  if (ids.length === 0) {
    return new Map<string, string>();
  }
  const { rows } = await authDb.execute<{ id: string; email: string }>(sql`
    select id::text, email from users
    where id = any(${sql.param(ids)}::uuid[]) and email_verified and not banned
  `);
  return new Map(rows.map((row) => [row.id, row.email]));
}

const events = (entries: Array<FeedEntry>) =>
  entries.map((entry) => {
    const { text, href } = describe(entry);
    return {
      title: text,
      meta: [byline(entry), entry.polity]
        .filter((part) => part != null)
        .join(", "),
      url: link(href ?? "/notifications"),
    };
  });

const unsubscribeHeaders = (url: string) => ({
  "List-Unsubscribe": `<${url}>`,
  "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
});

async function claim(
  key: string,
  userId: string,
  kind: "direct" | "digest",
  ids: Array<string>,
  period: { start: string; end: string } | null,
) {
  const { rows } = await db.execute<{ id: string }>(sql`
    insert into email_send (key, user_id, kind, notification_ids, period_start, period_end)
    values (${key}, ${userId}::uuid, ${kind}::email_kind, ${sql.param(ids)}::uuid[],
            ${period?.start ?? null}::timestamptz, ${period?.end ?? null}::timestamptz)
    on conflict (key) do update set status = 'sending', attempts = email_send.attempts + 1, error = null
      where email_send.status = 'failed' and email_send.attempts < 3
    returning id::text
  `);
  return rows[0]?.id ?? null;
}

async function settle(id: string, failure: unknown) {
  await db.execute(sql`
    update email_send set
      status = ${failure == null ? "sent" : "failed"}::email_status,
      sent_at = case when ${failure == null} then now() end,
      error = ${failure == null ? null : String(failure).slice(0, 2000)}
    where id = ${id}::uuid
  `);
}

async function sendDirect() {
  await db.execute(sql`
    update notification n set emailed_at = now()
    where n.reason = 'direct' and n.emailed_at is null
      and (n.read_at is not null
           or exists (select from notification_setting s where s.user_id = n.user_id and not s.direct_email))
  `);
  const { rows: due } = await db.execute<{ user_id: string }>(sql`
    select user_id::text from notification
    where reason = 'direct' and emailed_at is null
    group by user_id
    having max(created_at) < now() - interval '2 minutes' or min(created_at) < now() - interval '15 minutes'
  `);
  const emails = await recipients(due.map((row) => row.user_id));
  let sent = 0;
  for (const { user_id: userId } of due) {
    const email = emails.get(userId);
    const { rows: picked } = await db.execute<{
      ids: Array<string> | null;
    }>(sql`
      with picked as (
        select id from notification
        where user_id = ${userId}::uuid and reason = 'direct' and emailed_at is null
        order by id
        for update skip locked
      ), marked as (
        update notification n set emailed_at = now() from picked where n.id = picked.id returning n.id
      )
      select array_agg(id::text order by id) as ids from marked
    `);
    const ids = picked[0]?.ids ?? [];
    if (email == null || ids.length === 0) {
      continue;
    }
    const id = await claim(
      `direct:${userId}:${ids.at(-1) ?? ""}`,
      userId,
      "direct",
      ids,
      null,
    );
    if (id == null) {
      continue;
    }
    const [{ entries }, setting] = await Promise.all([
      stream({
        activity: sql`a.id in (select activity_id from notification where id = any(${sql.param(ids)}::uuid[]))`,
        imports: null,
        before: null,
        limit: ids.length,
      }),
      settings(userId),
    ]);
    if (entries.length === 0) {
      await settle(id, null);
      continue;
    }
    const first = entries.length === 1 ? entries[0] : undefined;
    const unsubscribeUrl = link(
      `/unsubscribe/${setting.unsubscribe_token}?list=direct`,
    );
    let failure: unknown = null;
    try {
      await sendEmail(
        email,
        {
          subject:
            first == null
              ? m.notify_direct_subject({ count: entries.length })
              : describe(first).text,
          preheader: m.notify_direct_preheader({ count: entries.length }),
          heading: m.notify_direct_heading({ count: entries.length }),
          events: events(entries),
          action: { label: m.notify_open(), url: link("/notifications") },
          reason: m.notify_direct_reason(),
          unsubscribe: unsubscribeUrl,
        },
        unsubscribeHeaders(unsubscribeUrl),
      );
      sent += 1;
    } catch (error) {
      failure = error;
      await db.execute(sql`
        update notification set emailed_at = null where id = any(${sql.param(ids)}::uuid[])
      `);
    }
    await settle(id, failure);
  }
  return sent;
}

async function sendDigests() {
  const { rows: due } = await db.execute<{
    user_id: string;
    digest: "daily" | "weekly";
    unsubscribe_token: string;
    start: string;
    end: string;
  }>(sql`
    select user_id::text, digest::text, unsubscribe_token,
           coalesce(digest_through, now() - k.span)::text as start, date_trunc('minute', now())::text as "end"
    from notification_setting
    cross join lateral (
      select case digest when 'daily' then interval '1 day' else interval '7 days' end as span
    ) k
    where digest <> 'off' and (digest_through is null or digest_through <= now() - k.span + interval '10 minutes')
  `);
  const emails = await recipients(due.map((row) => row.user_id));
  let sent = 0;
  for (const row of due) {
    const { rows: advanced } = await db.execute<{ user_id: string }>(sql`
      update notification_setting set digest_through = ${row.end}::timestamptz
      where user_id = ${row.user_id}::uuid
        and (digest_through is null or digest_through = ${row.start}::timestamptz)
      returning user_id
    `);
    const email = emails.get(row.user_id);
    if (email == null || advanced.length === 0) {
      continue;
    }
    const found = await stream({
      ...watchedStream(row.user_id),
      before: row.end,
      after: row.start,
      limit: DIGEST_EVENTS,
    });
    if (found.entries.length === 0) {
      continue;
    }
    const id = await claim(
      `digest:${row.user_id}:${row.start}`,
      row.user_id,
      "digest",
      [],
      { start: row.start, end: row.end },
    );
    if (id == null) {
      continue;
    }
    const daily = row.digest === "daily";
    const count = found.entries.length;
    const unsubscribeUrl = link(
      `/unsubscribe/${row.unsubscribe_token}?list=digest`,
    );
    let failure: unknown = null;
    try {
      await sendEmail(
        email,
        {
          subject: daily
            ? m.notify_digest_subject_daily({ count })
            : m.notify_digest_subject_weekly({ count }),
          preheader: m.notify_digest_preheader({ count }),
          heading: daily
            ? m.notify_digest_heading_daily()
            : m.notify_digest_heading_weekly(),
          ...(found.next != null && {
            body: m.notify_digest_more({ count: DIGEST_EVENTS }),
          }),
          events: events(found.entries),
          action: { label: m.notify_digest_open(), url: link("/watchlist") },
          reason: daily
            ? m.notify_digest_reason_daily()
            : m.notify_digest_reason_weekly(),
          unsubscribe: unsubscribeUrl,
        },
        unsubscribeHeaders(unsubscribeUrl),
      );
      sent += 1;
    } catch (error) {
      failure = error;
      await db.execute(sql`
        update notification_setting set digest_through = ${row.start}::timestamptz
        where user_id = ${row.user_id}::uuid and digest_through = ${row.end}::timestamptz
      `);
    }
    await settle(id, failure);
  }
  return sent;
}

export async function deliver() {
  return { direct: await sendDirect(), digests: await sendDigests() };
}
