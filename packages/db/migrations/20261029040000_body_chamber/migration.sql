set local lock_timeout = '10s';
--> statement-breakpoint
create table body_chamber (
  body_id uuid not null references body (id) on delete cascade,
  of_body_id uuid not null references body (id),
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange not null generated always as (daterange(valid_from, valid_to, '[)')) stored,
  source_id uuid not null references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (body_id, valid without overlaps)
);
--> statement-breakpoint
select ea_version('body_chamber');
