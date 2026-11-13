create type body_control as enum ('majority', 'minority', 'coalition', 'noc', 'mayor');
--> statement-breakpoint
create type composition_bucket as enum ('independent', 'other', 'vacant');
--> statement-breakpoint
create table body_composition (
  id uuid primary key,
  body_id uuid not null references body (id) on delete cascade,
  as_of date not null,
  as_of_precision date_precision not null default 'day',
  seats integer not null check (seats >= 0),
  control body_control,
  control_label text,
  control_derived boolean not null default false,
  source_id uuid not null references source (id),
  locator text not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  unique (body_id, as_of, source_id),
  check (not control_derived or control is not null)
);
--> statement-breakpoint
create index body_composition_changeset_id_idx on body_composition (changeset_id);
--> statement-breakpoint
select ea_version('body_composition'::regclass);
--> statement-breakpoint
alter table body_composition_history replica identity full;
--> statement-breakpoint
create table body_composition_party (
  id uuid primary key,
  composition_id uuid not null references body_composition (id) on delete cascade,
  party_id uuid references party (id),
  bucket composition_bucket,
  seats integer not null check (seats >= 0),
  role government_party_role,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  unique nulls not distinct (composition_id, party_id, bucket),
  check (num_nonnulls(party_id, bucket) = 1),
  check (bucket is distinct from 'vacant' or role is null)
);
--> statement-breakpoint
create index body_composition_party_party_idx on body_composition_party (party_id);
--> statement-breakpoint
create index body_composition_party_changeset_id_idx on body_composition_party (changeset_id);
--> statement-breakpoint
select ea_version('body_composition_party'::regclass);
--> statement-breakpoint
alter table body_composition_party_history replica identity full;
