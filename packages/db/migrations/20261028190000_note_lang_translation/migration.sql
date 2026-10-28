alter table note add column lang text not null default 'en';
--> statement-breakpoint
alter table note_history rename to note_history_before;
--> statement-breakpoint
create table note_history (like note, recorded tstzrange not null, superseded_by uuid not null);
--> statement-breakpoint
insert into note_history (id, entity_id, kind, body_md, valid_from, valid_from_precision, valid_to, valid_to_precision, valid,
                          changeset_id, recorded_from, lang, recorded, superseded_by)
select id, entity_id, kind, body_md, valid_from, valid_from_precision, valid_to, valid_to_precision, valid,
       changeset_id, recorded_from, 'en', recorded, superseded_by
from note_history_before;
--> statement-breakpoint
drop table note_history_before;
--> statement-breakpoint
create type translation_origin as enum ('official', 'human', 'machine');
--> statement-breakpoint
create table translation (
  source_md5 text not null,
  lang text not null,
  body text not null,
  origin translation_origin not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (source_md5, lang),
  check (source_md5 ~ '^[0-9a-f]{32}$'),
  check (lang <> 'en')
);
--> statement-breakpoint
select ea_version('translation');
