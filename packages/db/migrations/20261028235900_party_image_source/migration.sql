set local lock_timeout = '10s';
--> statement-breakpoint
alter table party_image add column dated_url text;
--> statement-breakpoint
alter table party_image drop constraint party_image_dated_by_check;
--> statement-breakpoint
alter table party_image add constraint party_image_dated_by_check check (dated_by in ('wikidata', 'file', 'source', 'next_logo'));
--> statement-breakpoint
alter table party_image add constraint party_image_dated_url_check check ((dated_by = 'source') = (dated_url is not null));
--> statement-breakpoint
alter table party_image_history rename to party_image_history_before;
--> statement-breakpoint
create table party_image_history (like party_image, recorded tstzrange not null, superseded_by uuid not null);
--> statement-breakpoint
insert into party_image_history (id, party_id, path, width, height, license, artist, source_url, source_id, valid_from,
                                 valid_from_precision, valid_to, valid_to_precision, valid, dated_by, uploaded_at, changeset_id,
                                 recorded_from, dated_url, recorded, superseded_by)
select id, party_id, path, width, height, license, artist, source_url, source_id, valid_from, valid_from_precision,
       valid_to, valid_to_precision, valid, dated_by, uploaded_at, changeset_id, recorded_from, null, recorded, superseded_by
from party_image_history_before;
--> statement-breakpoint
drop table party_image_history_before;
