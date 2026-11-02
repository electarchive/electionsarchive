create type account_limit_kind as enum ('cap', 'probation', 'no_upload', 'no_talk', 'no_artifacts');--> statement-breakpoint
create table account_limit (
  id uuid primary key default uuidv7(),
  user_id uuid not null references users (id) on delete cascade,
  kind account_limit_kind not null,
  value jsonb,
  reason text not null check (length(reason) between 1 and 2000),
  set_by uuid not null references users (id),
  created_at timestamptz not null default now(),
  expires_at timestamptz,
  lifted_by uuid references users (id),
  lifted_at timestamptz,
  check (kind <> 'cap' or jsonb_typeof(value -> 'proposals') = 'number'),
  check ((lifted_by is null) = (lifted_at is null))
);--> statement-breakpoint
create index account_limit_user_id_idx on account_limit (user_id, created_at desc);--> statement-breakpoint
create index proposal_author_submitted_idx on proposal (author_user_id, submitted_at) where submitted_at is not null;--> statement-breakpoint
create function contributor_level(target uuid) returns smallint
language sql stable as $$
  with tally as (
    select count(*) filter (where state in ('approved', 'published')) as accepted,
           count(*) filter (where state = 'rejected') + 3 * count(*) filter (where state = 'reverted') as against
    from proposal
    where author_user_id = target and submitted_at is not null
  )
  select (case
    when exists (select from user_role where user_id = target and role in ('admin', 'reviewer')) then 4
    when accepted >= 100 and accepted >= 0.95 * (accepted + against) then 3
    when accepted >= 20 and accepted >= 0.9 * (accepted + against) then 2
    when accepted >= 3 then 1
    else 0
  end)::smallint
  from tally
$$;--> statement-breakpoint
create function account_limited(target uuid, wanted account_limit_kind) returns boolean
language sql stable as $$
  select exists (
    select from account_limit
    where user_id = target and kind = wanted and lifted_at is null
      and (expires_at is null or expires_at > now())
  )
$$;
