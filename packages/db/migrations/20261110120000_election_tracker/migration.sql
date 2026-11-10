create type cadence_selection as enum ('elected', 'appointed', 'landowner', 'town_meeting', 'abolished');
--> statement-breakpoint
create type cadence_runoff as enum ('none', 'majority', 'threshold', 'instant');
--> statement-breakpoint
create type results_platform_access as enum ('open', 'browser', 'blocked', 'manual', 'none');
--> statement-breakpoint
create table election_cadence (
  id uuid primary key,
  polity_id uuid not null references polity (id),
  government_kind text,
  function text,
  name_pattern text,
  body_id uuid references body (id),
  office_id uuid references office (id),
  selection cadence_selection not null default 'elected',
  period_years integer check (period_years between 1 and 8),
  phase integer,
  month integer check (month between 1 and 12),
  day_rule text,
  classes integer not null default 1 check (classes >= 1),
  term_years integer,
  partisan boolean,
  runoff cadence_runoff not null default 'none',
  runoff_threshold numeric,
  runoff_rule text,
  administered_by text,
  valid_from date,
  valid_to date,
  source_id uuid not null references source (id),
  locator text not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check (phase is null or (period_years is not null and phase between 0 and period_years - 1)),
  check (government_kind is not null or (function is null and name_pattern is null)),
  check (valid_to is null or valid_from is null or valid_to > valid_from)
);
--> statement-breakpoint
create index election_cadence_polity_idx on election_cadence (polity_id, government_kind);
--> statement-breakpoint
select ea_version('election_cadence'::regclass);
--> statement-breakpoint
create table results_access (
  id uuid primary key,
  polity_id uuid not null references polity (id),
  serves text not null default 'administered',
  platform text not null,
  url_pattern text not null,
  format text not null,
  access results_platform_access not null,
  first_date date,
  last_date date,
  checked_at timestamptz not null,
  evidence text,
  valid_from date,
  valid_to date,
  source_id uuid references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check (valid_to is null or valid_from is null or valid_to > valid_from)
);
--> statement-breakpoint
create index results_access_polity_idx on results_access (polity_id);
--> statement-breakpoint
select ea_version('results_access'::regclass);
