create index if not exists candidacy_member_of_party_idx on candidacy (member_of_party_id) where member_of_party_id is not null;
--> statement-breakpoint
create index if not exists contest_party_scope_idx on contest (party_scope_id) where party_scope_id is not null;
--> statement-breakpoint
create index if not exists alliance_member_party_idx on alliance_member (party_id);
--> statement-breakpoint
create index if not exists candidacy_nomination_party_idx on candidacy_nomination (party_id);
--> statement-breakpoint
create index if not exists tenure_affiliation_party_idx on tenure_affiliation (party_id);
