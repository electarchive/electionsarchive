set local lock_timeout = '10s';
--> statement-breakpoint
alter table party_image add column is_light boolean not null default false;
--> statement-breakpoint
alter table party_image_history rename to party_image_history_before;
--> statement-breakpoint
create table party_image_history (like party_image, recorded tstzrange not null, superseded_by uuid not null);
--> statement-breakpoint
insert into party_image_history (id, party_id, path, width, height, license, artist, source_url, source_id, valid_from,
                                 valid_from_precision, valid_to, valid_to_precision, valid, dated_by, uploaded_at, changeset_id,
                                 recorded_from, dated_url, is_light, recorded, superseded_by)
select id, party_id, path, width, height, license, artist, source_url, source_id, valid_from, valid_from_precision,
       valid_to, valid_to_precision, valid, dated_by, uploaded_at, changeset_id, recorded_from, dated_url, false, recorded,
       superseded_by
from party_image_history_before;
--> statement-breakpoint
drop table party_image_history_before;
