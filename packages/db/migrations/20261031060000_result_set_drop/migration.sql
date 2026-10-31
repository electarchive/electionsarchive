create table result_set_drop (
  result_set_id uuid primary key,
  source_id uuid not null,
  reason text not null check (reason <> ''),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);--> statement-breakpoint
create index result_set_drop_changeset_id_idx on result_set_drop (changeset_id);--> statement-breakpoint
select ea_version('result_set_drop'::regclass);
