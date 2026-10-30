set lock_timeout = '10s';--> statement-breakpoint
drop table translation_history;--> statement-breakpoint
alter table translation drop constraint translation_lang_check;--> statement-breakpoint
alter table translation add column source_lang text not null default 'en';--> statement-breakpoint
alter table translation add column source_id uuid references source (id);--> statement-breakpoint
alter table translation add column stale_from text;--> statement-breakpoint
alter table translation
  add constraint translation_stale_from_check check (stale_from ~ '^[0-9a-f]{32}$' and stale_from <> source_md5),
  add constraint translation_lang_check check (lang <> source_lang),
  add constraint translation_lang_bcp47 check (lang ~ '^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$'),
  add constraint translation_source_lang_bcp47 check (source_lang ~ '^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$'),
  add constraint translation_body_nfc check (body is nfc normalized),
  add constraint translation_official_source check (origin <> 'official' or source_id is not null);--> statement-breakpoint
create table translation_history (like translation, recorded tstzrange not null, superseded_by uuid not null);--> statement-breakpoint
alter table note add constraint note_lang_bcp47
  check (lang ~ '^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$') not valid;--> statement-breakpoint
alter table note add constraint note_body_nfc check (body_md is nfc normalized) not valid;
