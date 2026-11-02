set lock_timeout = '10s';--> statement-breakpoint
create type notification_reason as enum ('direct', 'watched');--> statement-breakpoint
create type digest_cadence as enum ('off', 'daily', 'weekly');--> statement-breakpoint
create type email_kind as enum ('direct', 'digest');--> statement-breakpoint
create type email_status as enum ('sending', 'sent', 'failed');--> statement-breakpoint
create table notification_setting (
  user_id uuid primary key references users (id) on delete cascade,
  direct_email boolean not null default true,
  digest digest_cadence not null default 'off',
  unsubscribe_token text not null unique
    default replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', ''),
  digest_through timestamptz,
  updated_at timestamptz not null default now()
);--> statement-breakpoint
insert into notification_setting (user_id, digest)
select user_id, 'daily' from profile where digest;--> statement-breakpoint
alter table profile drop column digest;--> statement-breakpoint
create table notification (
  id uuid primary key default uuidv7(),
  user_id uuid not null references users (id) on delete cascade,
  activity_id uuid not null references activity (id) on delete cascade,
  reason notification_reason not null,
  created_at timestamptz not null default now(),
  read_at timestamptz,
  emailed_at timestamptz,
  unique (user_id, activity_id)
);--> statement-breakpoint
create index notification_user_id_idx on notification (user_id, id desc);--> statement-breakpoint
create index notification_unread_idx on notification (user_id) where read_at is null;--> statement-breakpoint
create index notification_unemailed_idx on notification (created_at) where reason = 'direct' and emailed_at is null;--> statement-breakpoint
create table email_send (
  id uuid primary key default uuidv7(),
  key text not null unique,
  user_id uuid references users (id) on delete set null,
  kind email_kind not null,
  notification_ids uuid[] not null default '{}',
  period_start timestamptz,
  period_end timestamptz,
  status email_status not null default 'sending',
  attempts smallint not null default 1,
  error text,
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  check ((status = 'sent') = (sent_at is not null))
);--> statement-breakpoint
create index email_send_user_id_idx on email_send (user_id, created_at desc);--> statement-breakpoint
create function activity_fanout() returns trigger
language plpgsql as $$
begin
  insert into notification (user_id, activity_id, reason)
  select distinct x, new.id, 'direct'::notification_reason
  from unnest(
    new.notify_user_ids
    || array(
      select p.author_user_id from proposal p
      where p.id = new.proposal_id and p.author_user_id is not null
        and (p.author_user_id is distinct from new.actor_user_id
             or new.kind in ('proposal_published', 'proposal_reverted'))
        and new.kind in ('changes_requested', 'proposal_rejected', 'proposal_published', 'proposal_reverted', 'commented')
    )
    || array(
      select r.author_user_id from comment c join comment r on r.id = c.parent_id
      where c.id = new.comment_id and r.author_user_id is not null
    )
  ) x
  where x is distinct from new.actor_user_id
     or new.kind in ('proposal_published', 'proposal_reverted')
  on conflict do nothing;
  insert into notification (user_id, activity_id, reason)
  select distinct w.user_id, new.id, 'watched'::notification_reason
  from watch w
  where w.user_id is distinct from new.actor_user_id
    and case w.target_kind
      when 'proposal' then w.target_id = new.proposal_id
      when 'user' then w.target_id = new.actor_user_id
      when 'bounty' then w.target_id = new.bounty_id
      when 'entity' then w.target_id = any(new.entity_ids)
        or w.target_id = (select t.entity_id from thread t where t.id = new.thread_id)
      when 'polity' then w.target_id = any(new.polity_ids)
        or (w.descendants and cardinality(new.polity_ids) > 0 and w.target_id in (
          with recursive up(id) as (
            select unnest(new.polity_ids)
            union
            select pp.parent_id from polity_parent pp join up on pp.polity_id = up.id
          )
          select id from up
        ))
      else false
    end
  on conflict do nothing;
  return null;
end
$$;--> statement-breakpoint
create trigger activity_fanout after insert on activity
for each row execute function activity_fanout();
