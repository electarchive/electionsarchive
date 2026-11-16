create type census_world_status as enum ('own', 'within', 'none');
--> statement-breakpoint
create table census_world (
  iso_code text primary key check (iso_code ~ '^[A-Z]{2}$'),
  name_en text not null check (btrim(name_en) <> ''),
  status census_world_status not null,
  within_iso text references census_world (iso_code) deferrable initially deferred,
  reason text,
  electorate bigint check (electorate >= 0),
  electorate_date date,
  electorate_precision date_precision,
  electorate_source_id uuid references source (id),
  electorate_locator text,
  source_id uuid not null references source (id),
  locator text not null check (btrim(locator) <> ''),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check ((status = 'within') = (within_iso is not null)),
  check (within_iso is distinct from iso_code),
  check (status = 'own' or btrim(coalesce(reason, '')) <> ''),
  check ((electorate is null) = (electorate_date is null)),
  check (electorate is null or (electorate_source_id is not null and btrim(coalesce(electorate_locator, '')) <> ''))
);
--> statement-breakpoint
create index census_world_changeset_id_idx on census_world (changeset_id);
--> statement-breakpoint
select ea_version('census_world'::regclass);
--> statement-breakpoint
alter table census_world_history replica identity full;
