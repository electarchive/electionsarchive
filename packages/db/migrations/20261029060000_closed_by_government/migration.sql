set local lock_timeout = '10s';
--> statement-breakpoint
alter type party_relation_kind add value if not exists 'closed_by_government';
--> statement-breakpoint
alter table party_relation drop constraint party_relation_check;
--> statement-breakpoint
alter table party_relation add constraint party_relation_check check ((kind::text in ('closed_by_court', 'closed_by_government')) = (to_party_id is null));
