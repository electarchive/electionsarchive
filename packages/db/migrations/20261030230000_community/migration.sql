set lock_timeout = '10s';--> statement-breakpoint
create type thread_kind as enum ('proposal', 'talk');--> statement-breakpoint
create type thread_status as enum ('open', 'resolved');--> statement-breakpoint
create type watch_target as enum ('entity', 'polity', 'proposal', 'bounty', 'user');--> statement-breakpoint
create table thread (
  id uuid primary key default uuidv7(),
  kind thread_kind not null,
  proposal_id uuid references proposal (id) on delete cascade,
  entity_id uuid,
  title text,
  status thread_status not null default 'open',
  author_user_id uuid references users (id) on delete set null,
  created_at timestamp(3) with time zone not null default now(),
  updated_at timestamp(3) with time zone not null default now(),
  constraint thread_kind_check check (
    case kind
      when 'proposal' then proposal_id is not null and entity_id is null and title is null
      else proposal_id is null and entity_id is not null and char_length(title) between 1 and 200
    end
  )
);--> statement-breakpoint
create unique index thread_proposal_id_key on thread (proposal_id);--> statement-breakpoint
create index thread_entity_id_idx on thread (entity_id, updated_at desc) where kind = 'talk';--> statement-breakpoint
create table comment (
  id uuid primary key default uuidv7(),
  thread_id uuid not null references thread (id) on delete cascade,
  parent_id uuid references comment (id) on delete cascade,
  author_user_id uuid references users (id) on delete set null,
  agent_key_id uuid references apikeys (id) on delete set null,
  agent_name text,
  agent_model text,
  revision_n integer,
  body_md text not null,
  anchor jsonb,
  suggestion jsonb,
  accepted_revision integer,
  created_at timestamp(3) with time zone not null default now(),
  edited_at timestamp(3) with time zone,
  hidden_by uuid references users (id) on delete set null,
  hidden_at timestamp(3) with time zone,
  hidden_reason text,
  constraint comment_body_check check (char_length(body_md) between 1 and 10000),
  constraint comment_hidden_check check ((hidden_at is null) = (hidden_reason is null))
);--> statement-breakpoint
create index comment_thread_id_idx on comment (thread_id, created_at);--> statement-breakpoint
create index comment_author_user_id_idx on comment (author_user_id, created_at desc);--> statement-breakpoint
create table watch (
  user_id uuid not null references users (id) on delete cascade,
  target_kind watch_target not null,
  target_id uuid not null,
  descendants boolean not null default false,
  created_at timestamp(3) with time zone not null default now(),
  primary key (user_id, target_kind, target_id)
);--> statement-breakpoint
create index watch_target_idx on watch (target_kind, target_id);--> statement-breakpoint
alter type activity_kind add value if not exists 'commented';--> statement-breakpoint
alter type activity_kind add value if not exists 'topic_opened';--> statement-breakpoint
alter type activity_kind add value if not exists 'topic_resolved';--> statement-breakpoint
alter table activity
  add column thread_id uuid references thread (id) on delete cascade,
  add column comment_id uuid references comment (id) on delete cascade,
  add column notify_user_ids uuid[] not null default '{}';--> statement-breakpoint
create index activity_notify_user_ids_idx on activity using gin (notify_user_ids);--> statement-breakpoint
create index activity_thread_id_idx on activity (thread_id);--> statement-breakpoint
alter table profile add column digest boolean not null default false;
