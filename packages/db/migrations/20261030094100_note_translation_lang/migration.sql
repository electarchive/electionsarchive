set lock_timeout = '10s';--> statement-breakpoint
drop table translation_history;--> statement-breakpoint
drop table translation;--> statement-breakpoint
drop function translation_capture();--> statement-breakpoint
create table translation (
  source_md5 text not null,
  lang text not null,
  source_lang text not null default 'en',
  body text not null,
  origin translation_origin not null,
  source_id uuid references source (id),
  stale_from text,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  constraint translation_pkey primary key (source_md5, lang),
  constraint translation_source_md5_check check (source_md5 ~ '^[0-9a-f]{32}$'),
  constraint translation_stale_from_check check (stale_from ~ '^[0-9a-f]{32}$' and stale_from <> source_md5),
  constraint translation_lang_check check (lang <> source_lang),
  constraint translation_lang_bcp47 check (lang ~ '^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$'),
  constraint translation_source_lang_bcp47 check (source_lang ~ '^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$'),
  constraint translation_body_nfc check (body is nfc normalized),
  constraint translation_official_source check (origin <> 'official' or source_id is not null)
);--> statement-breakpoint
select ea_version('translation');--> statement-breakpoint
alter table note add constraint note_lang_bcp47
  check (lang ~ '^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$') not valid;--> statement-breakpoint
alter table note add constraint note_body_nfc check (body_md is nfc normalized) not valid;
