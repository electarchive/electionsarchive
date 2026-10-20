create type ballot_question_text_kind as enum ('text', 'summary', 'link');

create table ballot_question_text (
  id uuid primary key default uuidv7(),
  contest_id uuid not null references ballot_question (contest_id) on delete cascade,
  kind ballot_question_text_kind not null,
  lang text not null default 'en',
  body text,
  url text,
  source_id uuid not null references source (id),
  locator jsonb,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check (body is not null or url is not null),
  check ((kind = 'link') = (body is null))
);
create unique index ballot_question_text_value_idx
  on ballot_question_text (contest_id, kind, lang, source_id, url) nulls not distinct;
create index ballot_question_text_source_idx on ballot_question_text (source_id);

select ea_version('ballot_question_text'::regclass);
