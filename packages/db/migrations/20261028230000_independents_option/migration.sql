set local lock_timeout = '10s';
--> statement-breakpoint
alter type option_kind add value if not exists 'independents';
--> statement-breakpoint
alter table contest_option add column candidate_count integer check (candidate_count > 0);
--> statement-breakpoint
alter table contest_option_history rename to contest_option_history_before;
--> statement-breakpoint
create table contest_option_history (like contest_option, recorded tstzrange not null, superseded_by uuid not null);
--> statement-breakpoint
insert into contest_option_history (id, k, contest_id, kind, parent_option_id, party_id, alliance_id, ballot_position,
                                    ballot_label, changeset_id, recorded_from, candidate_count, recorded, superseded_by)
select id, k, contest_id, kind, parent_option_id, party_id, alliance_id, ballot_position, ballot_label, changeset_id,
       recorded_from, null, recorded, superseded_by
from contest_option_history_before;
--> statement-breakpoint
drop table contest_option_history_before;
