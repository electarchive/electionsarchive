set local lock_timeout = '10s';
--> statement-breakpoint
create table party_image (
  id uuid primary key default uuidv7(),
  party_id uuid not null references party (id),
  path text not null,
  width integer not null check (width > 0),
  height integer not null check (height > 0),
  license text not null,
  artist text,
  source_url text not null,
  source_id uuid references source (id),
  valid_from date,
  valid_from_precision date_precision not null default 'unknown',
  valid_to date,
  valid_to_precision date_precision not null default 'unknown',
  valid daterange generated always as (case when valid_from is not null or valid_to is not null then daterange(valid_from, valid_to, '[)') end) stored,
  dated_by text check (dated_by in ('wikidata', 'file', 'next_logo')),
  uploaded_at timestamptz,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check ((dated_by is null) = (valid_from is null and valid_to is null))
);
--> statement-breakpoint
create index party_image_party_idx on party_image (party_id);
--> statement-breakpoint
select ea_version('party_image'::regclass);
