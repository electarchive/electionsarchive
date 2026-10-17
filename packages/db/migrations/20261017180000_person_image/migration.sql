create table person_image (
  id uuid primary key default uuidv7(),
  person_id uuid not null references person (id),
  path text not null,
  license text not null,
  artist text,
  source_url text not null,
  source_id uuid references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);
create index person_image_person_idx on person_image (person_id);

select ea_version('person_image'::regclass);
