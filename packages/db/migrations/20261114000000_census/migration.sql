create type census_category as enum (
  'legislature', 'executive', 'council', 'special_purpose', 'judicial', 'law_enforcement',
  'non_territorial', 'indirect', 'direct_democracy', 'nominating'
);
--> statement-breakpoint
create type census_disposition as enum ('blocked', 'out_of_scope');
--> statement-breakpoint
create type census_access_role as enum ('administers', 'publishes');
--> statement-breakpoint
create table census_tier (
  id uuid primary key,
  polity_id uuid not null references polity (id),
  key text not null,
  name text not null,
  name_en text,
  parent_tier_id uuid references census_tier (id),
  within_polity_id uuid references polity (id),
  polity_kind polity_kind,
  scheme_code text references scheme (code),
  elects boolean not null default true,
  valid_from date,
  valid_to date,
  source_id uuid not null references source (id),
  locator text not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  unique (polity_id, key),
  check (parent_tier_id is distinct from id),
  check (valid_to is null or valid_from is null or valid_to > valid_from)
);
--> statement-breakpoint
create index census_tier_parent_idx on census_tier (parent_tier_id);
--> statement-breakpoint
select ea_version('census_tier'::regclass);
--> statement-breakpoint
create table census_tier_count (
  id uuid primary key,
  tier_id uuid not null references census_tier (id),
  within_polity_id uuid references polity (id),
  date date not null,
  date_precision date_precision not null default 'day',
  value integer not null check (value >= 0),
  source_id uuid not null references source (id),
  locator text not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  unique nulls not distinct (tier_id, within_polity_id, date, source_id)
);
--> statement-breakpoint
select ea_version('census_tier_count'::regclass);
--> statement-breakpoint
create table census_class (
  id uuid primary key,
  polity_id uuid not null references polity (id),
  tier_id uuid not null references census_tier (id),
  key text not null,
  name text not null,
  name_en text,
  category census_category not null,
  goal text not null,
  valid_from date,
  valid_to date,
  first_held date,
  earliest_record date,
  contests_per_unit integer not null default 1 check (contests_per_unit >= 1),
  measure jsonb not null default '{}',
  finest_scheme_code text references scheme (code),
  finest_note text,
  boundary_source_id uuid references source (id),
  disposition census_disposition,
  disposition_reason text,
  est_votes bigint check (est_votes >= 0),
  effort smallint check (effort between 1 and 5),
  note text,
  source_id uuid not null references source (id),
  locator text not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  unique (polity_id, key),
  check (goal in ('national', 'subnational', 'local', 'referendums', 'ep')),
  check (jsonb_typeof(measure) = 'object'),
  check (disposition is null or btrim(coalesce(disposition_reason, '')) <> ''),
  check (valid_to is null or valid_from is null or valid_to > valid_from)
);
--> statement-breakpoint
create index census_class_tier_idx on census_class (tier_id);
--> statement-breakpoint
select ea_version('census_class'::regclass);
--> statement-breakpoint
create table census_match (
  id uuid primary key,
  class_id uuid not null references census_class (id),
  election_kinds election_kind[],
  contest_kinds contest_kind[],
  body_kinds body_kind[],
  office_kinds office_kind[],
  polity_kinds polity_kind[],
  within_polity_id uuid references polity (id),
  iso_pattern text,
  schemes text[],
  except_codes text[],
  name_pattern text,
  body_id uuid references body (id),
  office_id uuid references office (id),
  precedence integer not null default 0,
  valid_from date,
  valid_to date,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check (num_nonnulls(election_kinds, contest_kinds, body_kinds, office_kinds, polity_kinds, iso_pattern, name_pattern, body_id, office_id) > 0),
  check (valid_to is null or valid_from is null or valid_to > valid_from)
);
--> statement-breakpoint
create index census_match_class_idx on census_match (class_id);
--> statement-breakpoint
select ea_version('census_match'::regclass);
--> statement-breakpoint
create table census_date (
  id uuid primary key,
  class_id uuid not null references census_class (id),
  within_polity_id uuid references polity (id),
  date date not null,
  date_precision date_precision not null default 'day',
  through date,
  election_kind election_kind,
  contests integer check (contests >= 0),
  seats integer check (seats >= 0),
  regions jsonb,
  deferred date[],
  note text,
  source_id uuid references source (id),
  locator text,
  quote text,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check (through is null or through >= date),
  check (through is null or contests is null),
  check (contests is null or source_id is not null),
  check (source_id is null or btrim(coalesce(locator, '')) <> '' or btrim(coalesce(quote, '')) <> ''),
  check (regions is null or jsonb_typeof(regions) = 'object')
);
--> statement-breakpoint
create index census_date_class_idx on census_date (class_id, date);
--> statement-breakpoint
select ea_version('census_date'::regclass);
--> statement-breakpoint
alter table census_tier_history replica identity full;
--> statement-breakpoint
alter table census_tier_count_history replica identity full;
--> statement-breakpoint
alter table census_class_history replica identity full;
--> statement-breakpoint
alter table census_match_history replica identity full;
--> statement-breakpoint
alter table census_date_history replica identity full;
--> statement-breakpoint
alter table election_cadence
  add column class_id uuid references census_class (id),
  add column electoral_system_id uuid references electoral_system (id),
  add column called boolean not null default false,
  add column vacancy_rule text,
  add column certify_days integer check (certify_days between 0 and 366);
--> statement-breakpoint
alter table election_cadence_history
  add column class_id uuid,
  add column electoral_system_id uuid,
  add column called boolean,
  add column vacancy_rule text,
  add column certify_days integer;
--> statement-breakpoint
create index election_cadence_class_idx on election_cadence (class_id);
--> statement-breakpoint
alter table results_access
  add column class_id uuid references census_class (id),
  add column role census_access_role not null default 'publishes',
  add column license text,
  add column finest_scheme_code text references scheme (code),
  add column boundary_source_id uuid references source (id);
--> statement-breakpoint
alter table results_access_history
  add column class_id uuid,
  add column role census_access_role,
  add column license text,
  add column finest_scheme_code text,
  add column boundary_source_id uuid;
--> statement-breakpoint
create index results_access_class_idx on results_access (class_id);
--> statement-breakpoint
create or replace function election_cadence_capture() returns trigger language plpgsql as $$
begin
  insert into election_cadence_history (id, polity_id, government_kind, function, name_pattern, body_id, office_id, selection, period_years, phase, month, day_rule, classes, term_years, partisan, runoff, runoff_threshold, runoff_rule, administered_by, valid_from, valid_to, source_id, locator, changeset_id, recorded_from, class_id, electoral_system_id, called, vacancy_rule, certify_days, recorded, superseded_by)
  select id, polity_id, government_kind, function, name_pattern, body_id, office_id, selection, period_years, phase, month, day_rule, classes, term_years, partisan, runoff, runoff_threshold, runoff_rule, administered_by, valid_from, valid_to, source_id, locator, changeset_id, recorded_from, class_id, electoral_system_id, called, vacancy_rule, certify_days, tstzrange(o.recorded_from, now()), ea_current_changeset() from old_rows o;
  return null;
end $$;
--> statement-breakpoint
create or replace function results_access_capture() returns trigger language plpgsql as $$
begin
  insert into results_access_history (id, polity_id, serves, platform, url_pattern, format, access, first_date, last_date, checked_at, evidence, valid_from, valid_to, source_id, changeset_id, recorded_from, class_id, role, license, finest_scheme_code, boundary_source_id, recorded, superseded_by)
  select id, polity_id, serves, platform, url_pattern, format, access, first_date, last_date, checked_at, evidence, valid_from, valid_to, source_id, changeset_id, recorded_from, class_id, role, license, finest_scheme_code, boundary_source_id, tstzrange(o.recorded_from, now()), ea_current_changeset() from old_rows o;
  return null;
end $$;
--> statement-breakpoint
create table contest_class (
  contest_id uuid primary key,
  class_id uuid not null,
  rank smallint not null
);
--> statement-breakpoint
create index contest_class_class_idx on contest_class (class_id);
--> statement-breakpoint
alter table coverage_goal add column class_id uuid;
--> statement-breakpoint
alter table coverage_polity add column classes jsonb not null default '[]';
--> statement-breakpoint
alter table coverage_government add column class_id uuid;
