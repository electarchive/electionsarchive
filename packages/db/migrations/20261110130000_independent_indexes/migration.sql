create index if not exists candidacy_independent_idx on candidacy (option_id) where is_independent;--> statement-breakpoint
create index if not exists contest_option_independent_label_idx on contest_option (id) where party_id is null and alliance_id is null and ballot_label ~* '^(independent|bağımsız|indépendant|unabhängig|indipendent)';
