set local lock_timeout = '10s';
--> statement-breakpoint
create table party_founding (
  party_id uuid primary key references party (id) on delete cascade,
  date date not null,
  date_precision date_precision not null,
  source_id uuid not null references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);
--> statement-breakpoint
select ea_version('party_founding'::regclass);
