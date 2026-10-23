alter table person_image
  add column taken_text text,
  add column taken_from date,
  add column taken_from_precision date_precision not null default 'unknown',
  add column taken_to date,
  add column taken_to_precision date_precision not null default 'unknown',
  add column uploaded_at timestamptz;

alter table person_image_history rename to person_image_history_before;
create table person_image_history (like person_image, recorded tstzrange not null, superseded_by uuid not null);
insert into person_image_history (id, person_id, path, license, artist, source_url, source_id, changeset_id,
                                  recorded_from, taken_from_precision, taken_to_precision, recorded, superseded_by)
select id, person_id, path, license, artist, source_url, source_id, changeset_id, recorded_from, 'unknown', 'unknown',
       recorded, superseded_by
from person_image_history_before;
drop table person_image_history_before;
