alter table person_party_membership drop constraint person_party_membership_pkey;
alter table person_party_membership
  add constraint person_party_membership_pkey primary key (person_id, party_id, valid without overlaps);
create index person_party_membership_party_idx on person_party_membership (party_id);

alter table tenure_affiliation drop constraint tenure_affiliation_pkey;
alter table tenure_affiliation
  add constraint tenure_affiliation_key unique (tenure_id, kind, party_id, valid without overlaps);
