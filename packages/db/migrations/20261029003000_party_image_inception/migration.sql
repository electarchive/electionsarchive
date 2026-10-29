set local lock_timeout = '10s';
--> statement-breakpoint
alter table party_image drop constraint party_image_dated_by_check;
--> statement-breakpoint
alter table party_image add constraint party_image_dated_by_check check (dated_by in ('wikidata', 'file', 'source', 'next_logo', 'inception'));
