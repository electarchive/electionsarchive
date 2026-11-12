set lock_timeout = '5s';
--> statement-breakpoint
create table area_kind (
  area_id uuid not null references area (id) on delete cascade,
  kind text not null check (kind ~ '^[a-z][a-z0-9_]*$'),
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange not null generated always as (daterange(valid_from, valid_to, '[)')) stored,
  source_id uuid references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (area_id, valid without overlaps)
);
--> statement-breakpoint
create index area_kind_changeset_id_idx on area_kind (changeset_id);
--> statement-breakpoint
select ea_version('area_kind'::regclass);
--> statement-breakpoint
create table area_membership (
  area_id uuid not null references area (id) on delete cascade,
  parent_id uuid not null references area (id),
  partial boolean not null default false,
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange not null generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check (area_id <> parent_id),
  primary key (area_id, parent_id, valid without overlaps)
);
--> statement-breakpoint
create index area_membership_parent_idx on area_membership (parent_id);
--> statement-breakpoint
create index area_membership_changeset_id_idx on area_membership (changeset_id);
--> statement-breakpoint
select ea_version('area_membership'::regclass);
--> statement-breakpoint
alter table scheme add column kinds jsonb check (jsonb_typeof(kinds) = 'object');
--> statement-breakpoint
alter table scheme add column code_system text check (code_system ~ '^[a-z][a-z0-9_]*$');
--> statement-breakpoint
alter table scheme_history rename to scheme_history_before;
--> statement-breakpoint
create table scheme_history (like scheme, recorded tstzrange not null, superseded_by uuid not null);
--> statement-breakpoint
insert into scheme_history (code, polity_id, kind, rank, noun, changeset_id, recorded_from, recorded, superseded_by)
select code, polity_id, kind, rank, noun, changeset_id, recorded_from, recorded, superseded_by
from scheme_history_before;
--> statement-breakpoint
drop table scheme_history_before;
