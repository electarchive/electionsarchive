alter type acquired_via add value if not exists 'indirect';
--> statement-breakpoint
create type seat_category as enum ('elected', 'appointed', 'ex_officio', 'indirect');
--> statement-breakpoint
create table body_size_part (
  body_id uuid not null references body (id) on delete cascade,
  category seat_category not null,
  seats integer not null,
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange not null generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (body_id, category, valid without overlaps)
);
--> statement-breakpoint
select ea_version('body_size_part');
