alter table live.ballots drop constraint ballots_stage_check;--> statement-breakpoint
alter table live.ballots add constraint ballots_stage_check check (stage in ('requested', 'issued', 'returned', 'accepted', 'rejected', 'remaining', 'tabulated', 'unreturned')) not valid;--> statement-breakpoint
alter table live.ballots validate constraint ballots_stage_check;
