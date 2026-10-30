create table row_drop (
  id uuid primary key,
  relation text not null check (relation in (
    'citation', 'label', 'party_color', 'party_image', 'person_image', 'translation', 'alliance_member',
    'candidacy_nomination', 'government_party', 'tenure_affiliation', 'area_event_member', 'party_relation',
    'party_affiliation'
  )),
  key jsonb not null check (jsonb_typeof(key) = 'object' and key <> '{}'),
  reason text not null check (reason <> ''),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);--> statement-breakpoint
create index row_drop_changeset_id_idx on row_drop (changeset_id);--> statement-breakpoint
select ea_version('row_drop'::regclass);
