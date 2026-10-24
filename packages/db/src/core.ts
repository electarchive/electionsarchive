import { daterange } from "./range.ts";
import { users } from "./schema.ts";
import { pgEnum, pgTable, uuid, integer, smallint, text, boolean, customType, date, jsonb, timestamp, numeric, real, char, bigint, geometry, index, uniqueIndex, foreignKey, primaryKey, unique, check, pgView } from "drizzle-orm/pg-core"
import { sql } from "drizzle-orm"

export const acquiredVia = pgEnum("acquired_via", ["elected", "appointed", "trustee", "succession", "list_replacement", "co_opted", "acting"])
export const allianceKind = pgEnum("alliance_kind", ["electoral_alliance", "joint_list", "federation", "apparentement"])
export const areaEventKind = pgEnum("area_event_kind", ["created", "dissolved", "renamed", "recoded", "split", "merged", "absorbed", "boundary_changed", "transferred"])
export const areaEventRole = pgEnum("area_event_role", ["from", "to"])
export const areaRelation = pgEnum("area_relation", ["equals", "within", "unlocated"])
export const authorKind = pgEnum("author_kind", ["pipeline", "user", "agent", "system"])
export const ballotQuestionTextKind = pgEnum("ballot_question_text_kind", ["text", "summary", "link"])
export const ballotQuestionType = pgEnum("ballot_question_type", ["initiative", "mandatory_referendum", "optional_referendum", "counter_proposal", "tie_break", "plebiscite", "recall_question"])
export const bodyEventKind = pgEnum("body_event_kind", ["dissolved", "suspended", "reconstituted", "replaced_by_appointed"])
export const bodyKind = pgEnum("body_kind", ["lower_house", "upper_house", "unicameral", "council", "electoral_college", "constituent_assembly", "appointed_assembly"])
export const calendar = pgEnum("calendar", ["gregorian", "julian", "rumi", "hijri", "french_republican"])
export const candidacyRole = pgEnum("candidacy_role", ["head", "running_mate", "list_member", "substitute"])
export const changesetKind = pgEnum("changeset_kind", ["import", "edit", "correction", "match", "digitization", "live"])
export const changesetStatus = pgEnum("changeset_status", ["draft", "pending_review", "published", "rejected", "superseded"])
export const contestKind = pgEnum("contest_kind", ["office", "body_seats", "list_allocation", "ballot_question", "elector_slate", "delegate_allocation"])
export const contestTerm = pgEnum("contest_term", ["full", "remainder"])
export const crosswalkFromKind = pgEnum("crosswalk_from_kind", ["boundary_set", "reporting_scheme"])
export const crosswalkMethod = pgEnum("crosswalk_method", ["exact", "registered_voters", "population", "area"])
export const datePrecision = pgEnum("date_precision", ["day", "month", "year", "unknown"])
export const electionKind = pgEnum("election_kind", ["general", "by_election", "primary", "caucus", "runoff", "referendum", "recall", "indirect", "local", "special"])
export const electionStatus = pgEnum("election_status", ["scheduled", "held", "postponed", "canceled", "annulled"])
export const endedVia = pgEnum("ended_via", ["term_end", "resigned", "died", "removed", "annulled", "dissolved", "expelled", "lost_seat", "disqualified", "withdrew", "elevated", "appointed"])
export const entityType = pgEnum("entity_type", ["polity", "area", "reporting_unit", "election", "contest", "contest_option", "result_set", "electoral_system", "party", "alliance", "person", "candidacy", "body", "office", "seat", "tenure", "source", "saved_view", "government"])
export const flagSeverity = pgEnum("flag_severity", ["info", "warning", "error"])
export const flagStatus = pgEnum("flag_status", ["open", "accepted", "resolved"])
export const geometryQuality = pgEnum("geometry_quality", ["official", "osm", "academic", "digitized", "synthetic"])
export const governmentPartyRole = pgEnum("government_party_role", ["head", "member", "support"])
export const labelKind = pgEnum("label_kind", ["recognition", "integrity", "data_quality"])
export const nameKind = pgEnum("name_kind", ["official", "native", "english", "historical", "transliteration", "abbreviation", "ballot", "short"])
export const nominationKind = pgEnum("nomination_kind", ["nominated", "endorsed", "supported"])
export const noteKind = pgEnum("note_kind", ["context", "caveat", "methodology", "editorial", "retired"])
export const officeKind = pgEnum("office_kind", ["head_of_state", "head_of_government", "governor", "mayor", "deputy_mayor", "muhtar", "speaker", "other"])
export const optionKind = pgEnum("option_kind", ["candidate", "ticket", "list", "legend", "answer", "write_in", "others", "none_of_the_above", "uncommitted"])
export const outcomeKind = pgEnum("outcome_kind", ["elected", "won", "advanced", "eliminated", "lost", "tied", "withdrew", "disqualified"])
export const partyAffiliationKind = pgEnum("party_affiliation_kind", ["ep_group", "federation", "international", "regional_branch", "coalition"])
export const partyKind = pgEnum("party_kind", ["party", "independent_group", "list_vehicle", "federation", "bloc", "nuance", "parliamentary_group", "caucus"])
export const partyRelationKind = pgEnum("party_relation_kind", ["merged_into", "split_from", "successor_of", "refounded_as", "absorbed_by", "closed_by_court", "list_vehicle_for"])
export const payloadKind = pgEnum("payload_kind", ["ops", "artifacts"])
export const polityKind = pgEnum("polity_kind", ["sovereign_state", "supranational", "federal_unit", "region", "county", "municipality", "submunicipal", "historical_state", "school_district", "judicial_district", "special_district"])
export const polityPeriodKind = pgEnum("polity_period_kind", ["constitutional_order", "military_rule", "occupation", "transitional"])
export const polityRelationKind = pgEnum("polity_relation_kind", ["succeeded_by", "merged_into", "split_into", "annexed_by", "reunified_into", "seceded_from", "consolidated_with"])
export const reportingUnitKind = pgEnum("reporting_unit_kind", ["standard", "abroad_mission", "customs_gate", "prison", "mobile", "postal_district", "aggregate"])
export const resultKind = pgEnum("result_kind", ["counted", "tacit", "show_of_hands", "appointed"])
export const resultSetStatus = pgEnum("result_set_status", ["active", "superseded", "annulled", "withdrawn"])
export const schemeKind = pgEnum("scheme_kind", ["administrative", "electoral_district", "precinct", "census", "synthetic"])
export const sourceKind = pgEnum("source_kind", ["official_body", "aggregator", "compendium", "news_agency", "academic", "community", "wikidata"])
export const stage = pgEnum("stage", ["live", "preliminary", "official", "certified", "court_revised", "recount", "alternative", "notional"])
export const tenureAffiliationKind = pgEnum("tenure_affiliation_kind", ["party", "parliamentary_group"])
export const tenureEventKind = pgEnum("tenure_event_kind", ["elected", "certified", "certificate_denied", "certificate_reversed", "sworn_in", "suspended", "removed", "trustee_appointed", "reinstated", "resigned", "died", "immunity_lifted", "arrested", "switched_party", "became_independent", "expelled", "term_ended"])
export const representationGapReason = pgEnum("representation_gap_reason", ["secession"])
export const unitAreaMethod = pgEnum("unit_area_method", ["source_code", "city_universe", "shape_contained"])


export const alliance = pgTable("alliance", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	kind: allianceKind().notNull(),
	electionId: uuid("election_id").references(() => election.id),
	parentAllianceId: uuid("parent_alliance_id"),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.parentAllianceId],
		foreignColumns: [table.id],
		name: "alliance_parent_alliance_id_fkey"
	}),
]);

export const allianceMember = pgTable("alliance_member", {
	allianceId: uuid("alliance_id").notNull().references(() => alliance.id, { onDelete: "cascade" } ),
	partyId: uuid("party_id").notNull().references(() => party.id),
	role: text(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.allianceId, table.partyId], name: "alliance_member_pkey"}),
]);

export const area = pgTable("area", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	k: integer().generatedAlwaysAsIdentity(),
	schemeCode: text("scheme_code").notNull().references(() => scheme.code),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	unique("area_k_key").on(table.k),]);

export const areaCode = pgTable("area_code", {
	areaId: uuid("area_id").notNull().references(() => area.id, { onDelete: "cascade" } ),
	code: text().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.areaId, table.valid], name: "area_code_pkey"}),
	index("area_code_code_idx").using("btree", table.code.asc().nullsLast()),
]);

export const areaEvent = pgTable("area_event", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	kind: areaEventKind().notNull(),
	date: date(),
	datePrecision: datePrecision("date_precision").default("day").notNull(),
	sourceId: uuid("source_id").references(() => source.id),
	notes: text(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const areaEventMember = pgTable("area_event_member", {
	eventId: uuid("event_id").notNull().references(() => areaEvent.id, { onDelete: "cascade" } ),
	areaId: uuid("area_id").notNull().references(() => area.id),
	role: areaEventRole().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.eventId, table.areaId, table.role], name: "area_event_member_pkey"}),
]);

export const areaGeometry = pgTable("area_geometry", {
	areaId: uuid("area_id").notNull().references(() => area.id, { onDelete: "cascade" } ),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	geom: customType({ dataType: () => 'geometry(MultiPolygon,4326)' })().notNull(),
	labelPoint: geometry("label_point"),
	quality: geometryQuality().notNull(),
	sourceId: uuid("source_id").references(() => source.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.areaId, table.valid], name: "area_geometry_pkey"}),
	index("area_geometry_geom_idx").using("gist", table.geom.asc().nullsLast()),
	index("area_geometry_synthetic_idx").using("btree", table.areaId.asc().nullsLast()).where(sql`(quality = 'synthetic'::geometry_quality)`),
]);

export const areaParent = pgTable("area_parent", {
	areaId: uuid("area_id").notNull().references(() => area.id, { onDelete: "cascade" } ),
	parentId: uuid("parent_id").notNull().references(() => area.id),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.areaId, table.valid], name: "area_parent_pkey"}),
	index("area_parent_parent_idx").using("btree", table.parentId.asc().nullsLast()),
]);

export const areaUniverse = pgTable("area_universe", {
	areaId: uuid("area_id").notNull().references(() => area.id),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	expected: integer().notNull(),
	method: unitAreaMethod().notNull(),
	sourceId: uuid("source_id").references(() => source.id),
	resultSetId: uuid("result_set_id").references(() => resultSet.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	shaped: boolean().notNull(),
}, (table) => [
	primaryKey({ columns: [table.areaId, table.shaped, table.valid], name: "area_universe_pkey"}),
check("area_universe_check", sql`((method = 'city_universe'::unit_area_method) = (result_set_id IS NOT NULL))`),check("area_universe_expected_check", sql`(expected > 0)`),]);

export const ballotQuestion = pgTable("ballot_question", {
	contestId: uuid("contest_id").primaryKey().references(() => contest.id, { onDelete: "cascade" } ),
	type: ballotQuestionType().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const ballotQuestionText = pgTable("ballot_question_text", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	contestId: uuid("contest_id").notNull().references(() => ballotQuestion.contestId, { onDelete: "cascade" } ),
	kind: ballotQuestionTextKind().notNull(),
	lang: text().default("en").notNull(),
	body: text(),
	url: text(),
	sourceId: uuid("source_id").notNull().references(() => source.id),
	locator: jsonb(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	index("ballot_question_text_source_idx").using("btree", table.sourceId.asc().nullsLast()),
	uniqueIndex("ballot_question_text_value_idx").using("btree", table.contestId.asc().nullsLast(), table.kind.asc().nullsLast(), table.lang.asc().nullsLast(), table.sourceId.asc().nullsLast(), table.url.asc().nullsLast()),
check("ballot_question_text_check", sql`((body IS NOT NULL) OR (url IS NOT NULL))`),check("ballot_question_text_check1", sql`((kind = 'link'::ballot_question_text_kind) = (body IS NULL))`),]);

export const body = pgTable("body", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	polityId: uuid("polity_id").notNull().references(() => polity.id),
	kind: bodyKind().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const bodyApportionment = pgTable("body_apportionment", {
	bodyId: uuid("body_id").notNull().references(() => body.id, { onDelete: "cascade" } ),
	areaId: uuid("area_id").notNull().references(() => area.id),
	seats: integer().notNull(),
	validFrom: date("valid_from").notNull(),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.bodyId, table.areaId, table.valid], name: "body_apportionment_pkey"}),
]);

export const bodyEvent = pgTable("body_event", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	bodyId: uuid("body_id").notNull().references(() => body.id),
	kind: bodyEventKind().notNull(),
	date: date(),
	datePrecision: datePrecision("date_precision").default("day").notNull(),
	sourceId: uuid("source_id").references(() => source.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const bodySize = pgTable("body_size", {
	bodyId: uuid("body_id").notNull().references(() => body.id, { onDelete: "cascade" } ),
	seats: integer().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.bodyId, table.valid], name: "body_size_pkey"}),
]);

export const boundarySet = pgTable("boundary_set", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	schemeCode: text("scheme_code").notNull().references(() => scheme.code),
	vintage: text().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	asOf: date("as_of").notNull(),
	tilesKey: text("tiles_key"),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	unique("boundary_set_scheme_code_vintage_key").on(table.schemeCode, table.vintage),]);

export const candidacy = pgTable("candidacy", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	personId: uuid("person_id").notNull().references(() => person.id),
	optionId: uuid("option_id").notNull().references(() => contestOption.id),
	role: candidacyRole().default("head").notNull(),
	listPosition: integer("list_position"),
	ballotName: text("ballot_name"),
	memberOfPartyId: uuid("member_of_party_id").references(() => party.id),
	isIndependent: boolean("is_independent").default(false).notNull(),
	isIncumbent: boolean("is_incumbent"),
	ballotLines: smallint("ballot_lines").default(1).notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	index("candidacy_option_idx").using("btree", table.optionId.asc().nullsLast()),
	index("candidacy_person_idx").using("btree", table.personId.asc().nullsLast()),
check("candidacy_ballot_lines_check", sql`((ballot_lines >= 1) AND (ballot_lines <= 3))`),]);

export const candidacyNomination = pgTable("candidacy_nomination", {
	candidacyId: uuid("candidacy_id").notNull().references(() => candidacy.id, { onDelete: "cascade" } ),
	partyId: uuid("party_id").notNull().references(() => party.id),
	kind: nominationKind().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.candidacyId, table.partyId], name: "candidacy_nomination_pkey"}),
]);

export const changeset = pgTable("changeset", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	authorKind: authorKind("author_kind").notNull(),
	authorUserId: uuid("author_user_id").references(() => users.id, { onDelete: "set null" } ),
	pipeline: text(),
	pipelineVersion: text("pipeline_version"),
	kind: changesetKind().notNull(),
	status: changesetStatus().default("draft").notNull(),
	summary: text().notNull(),
	rationale: text(),
	scope: jsonb().default({}).notNull(),
	payloadKind: payloadKind("payload_kind"),
	payload: jsonb(),
	artifactKeys: text("artifact_keys").array(),
	checks: jsonb(),
	createdAt: timestamp("created_at", { withTimezone: true }).default(sql`now()`).notNull(),
	publishedAt: timestamp("published_at", { withTimezone: true }),
	reviewedBy: uuid("reviewed_by").references(() => users.id, { onDelete: "set null" } ),
	reviewedAt: timestamp("reviewed_at", { withTimezone: true }),
});

export const channel = pgTable("channel", {
	id: smallint().primaryKey(),
	code: text().notNull(),
	description: text().notNull(),
}, (table) => [
	unique("channel_code_key").on(table.code),]);

export const citation = pgTable("citation", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	entityId: uuid("entity_id").notNull().references(() => entity.id, { onDelete: "cascade" } ),
	sourceId: uuid("source_id").notNull().references(() => source.id),
	locator: jsonb(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	index("citation_entity_idx").using("btree", table.entityId.asc().nullsLast()),
]);

export const contest = pgTable("contest", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	electionId: uuid("election_id").notNull().references(() => election.id),
	kind: contestKind().notNull(),
	officeId: uuid("office_id").references(() => office.id),
	bodyId: uuid("body_id").references(() => body.id),
	districtAreaId: uuid("district_area_id").references(() => area.id),
	seatId: uuid("seat_id").references(() => seat.id),
	boundarySetId: uuid("boundary_set_id").references(() => boundarySet.id),
	magnitude: integer(),
	partyScopeId: uuid("party_scope_id").references(() => party.id),
	roundNumber: smallint("round_number").default(1).notNull(),
	previousRoundId: uuid("previous_round_id"),
	allocationParentId: uuid("allocation_parent_id"),
	electoralSystemId: uuid("electoral_system_id").references(() => electoralSystem.id),
	rulesOverride: jsonb("rules_override"),
	defaultResultKind: resultKind("default_result_kind").default("counted").notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	term: contestTerm(),
	notional: boolean().default(false).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.allocationParentId],
		foreignColumns: [table.id],
		name: "contest_allocation_parent_id_fkey"
	}),
	foreignKey({
		columns: [table.previousRoundId],
		foreignColumns: [table.id],
		name: "contest_previous_round_id_fkey"
	}),
	index("contest_allocation_parent_idx").using("btree", table.allocationParentId.asc().nullsLast()),
	index("contest_body_idx").using("btree", table.bodyId.asc().nullsLast()),
	index("contest_district_area_idx").using("btree", table.districtAreaId.asc().nullsLast()),
	index("contest_election_idx").using("btree", table.electionId.asc().nullsLast()),
	index("contest_office_idx").using("btree", table.officeId.asc().nullsLast()),
	index("contest_previous_round_idx").using("btree", table.previousRoundId.asc().nullsLast()),
	index("contest_seat_idx").using("btree", table.seatId.asc().nullsLast()),
]);

export const contestOption = pgTable("contest_option", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	k: integer().generatedAlwaysAsIdentity(),
	contestId: uuid("contest_id").notNull().references(() => contest.id),
	kind: optionKind().notNull(),
	parentOptionId: uuid("parent_option_id"),
	partyId: uuid("party_id").references(() => party.id),
	allianceId: uuid("alliance_id").references(() => alliance.id),
	ballotPosition: integer("ballot_position"),
	ballotLabel: text("ballot_label"),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.parentOptionId],
		foreignColumns: [table.id],
		name: "contest_option_parent_option_id_fkey"
	}),
	index("contest_option_contest_idx").using("btree", table.contestId.asc().nullsLast()),
	index("contest_option_parent_option_idx").using("btree", table.parentOptionId.asc().nullsLast()),
	index("contest_option_party_idx").using("btree", table.partyId.asc().nullsLast()),
	unique("contest_option_k_key").on(table.k),]);

export const coverageBuild = pgTable("coverage_build", {
	id: boolean().default(true).primaryKey(),
	requestedAt: timestamp("requested_at", { withTimezone: true }),
	startedAt: timestamp("started_at", { withTimezone: true }),
	builtAt: timestamp("built_at", { withTimezone: true }),
}, (table) => [
check("coverage_build_id_check", sql`id`),]);

export const coverageCacheDone = pgTable("coverage_cache_done", {
	resultSetK: integer("result_set_k").primaryKey(),
});

export const coverageCacheMeasure = pgTable("coverage_cache_measure", {
	resultSetK: integer("result_set_k").notNull(),
	measure: smallint().notNull(),
}, (table) => [
	primaryKey({ columns: [table.resultSetK, table.measure], name: "coverage_cache_measure_pkey"}),
]);

export const coverageCacheShapes = pgTable("coverage_cache_shapes", {
	key: text().primaryKey(),
	signature: text().notNull(),
	units: integer().notNull(),
	shaped: integer().notNull(),
});

export const coverageCacheState = pgTable("coverage_cache_state", {
	fingerprint: text().notNull(),
});

export const coverageCacheUnit = pgTable("coverage_cache_unit", {
	resultSetK: integer("result_set_k").notNull(),
	unitK: integer("unit_k").notNull(),
}, (table) => [
	primaryKey({ columns: [table.resultSetK, table.unitK], name: "coverage_cache_unit_pkey"}),
]);

export const coverageCacheUnitK = pgTable("coverage_cache_unit_k", {
	unitK: integer("unit_k").primaryKey(),
});

export const coverageGoal = pgTable("coverage_goal", {
	polity: text().default("us").notNull(),
	goal: text().notNull(),
	state: text().notNull(),
	level: text().notNull(),
	year: integer().notNull(),
	total: integer().notNull(),
	complete: integer().notNull(),
	absent: integer().notNull(),
	bare: integer().notNull(),
	totals: integer().notNull(),
	partial: integer().notNull(),
	coarse: integer().notNull(),
	turnout: integer().notNull(),
	precincts: integer().notNull(),
	shaped: integer().notNull(),
	unshaped: integer().default(0).notNull(),
	units: integer().default(0).notNull(),
	undrawn: integer().default(0).notNull(),
	icpsr: integer().default(0).notNull(),
}, (table) => [
	primaryKey({ columns: [table.polity, table.goal, table.state, table.level, table.year], name: "coverage_goal_pkey"}),
]);

export const coveragePolity = pgTable("coverage_polity", {
	polity: text().primaryKey(),
	first: integer(),
	last: integer(),
	elections: integer().notNull(),
	contests: integer().notNull(),
	finest: text(),
	finestNoun: text("finest_noun"),
	regions: jsonb().default({}).notNull(),
	levels: jsonb().default([]).notNull(),
	notes: jsonb().default([]).notNull(),
	sources: jsonb().default({}).notNull(),
});

export const coverageGovernment = pgTable("coverage_government", {
	id: text().primaryKey(),
	state: text().notNull(),
	county: text(),
	kind: text().notNull(),
	name: text().notNull(),
	function: text(),
	population: integer(),
	active: boolean().notNull(),
	polityId: uuid("polity_id"),
	period: integer(),
	phase: integer(),
	month: integer(),
	basis: text().notNull(),
}, (table) => [
	index("coverage_government_polity_idx").using("btree", table.polityId.asc().nullsLast()),
	index("coverage_government_state_idx").using("btree", table.state.asc().nullsLast(), table.kind.asc().nullsLast()),
]);

export const coverageGovernmentYear = pgTable("coverage_government_year", {
	governmentId: text("government_id").notNull().references(() => coverageGovernment.id, { onDelete: "cascade" } ),
	year: integer().notNull(),
	expected: boolean().notNull(),
	held: integer().notNull(),
	precinct: integer().notNull(),
	precincts: integer().notNull(),
	shaped: integer().notNull(),
	missing: boolean().notNull(),
}, (table) => [
	primaryKey({ columns: [table.year, table.governmentId], name: "coverage_government_year_pkey"}),
]);

export const crosswalk = pgTable("crosswalk", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	fromKind: crosswalkFromKind("from_kind").notNull(),
	fromBoundarySetId: uuid("from_boundary_set_id").references(() => boundarySet.id),
	fromSchemeCode: text("from_scheme_code").references(() => scheme.code),
	fromAsOf: date("from_as_of"),
	toBoundarySetId: uuid("to_boundary_set_id").notNull().references(() => boundarySet.id),
	method: crosswalkMethod().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const crosswalkWeight = pgTable("crosswalk_weight", {
	crosswalkId: uuid("crosswalk_id").notNull().references(() => crosswalk.id, { onDelete: "cascade" } ),
	fromK: integer("from_k").notNull(),
	toK: integer("to_k").notNull(),
	weight: numeric().notNull(),
	exact: boolean().notNull(),
}, (table) => [
	primaryKey({ columns: [table.crosswalkId, table.fromK, table.toK], name: "crosswalk_weight_pkey"}),
]);

export const districtCounty = pgTable("district_county", {
	districtAreaId: uuid("district_area_id").notNull(),
	districtValid: customType({ dataType: () => 'daterange' })("district_valid").notNull(),
	countyAreaId: uuid("county_area_id").notNull(),
	countyValid: customType({ dataType: () => 'daterange' })("county_valid").notNull(),
	share: real().notNull(),
	rest: real(),
}, (table) => [
	primaryKey({ columns: [table.districtAreaId, table.districtValid, table.countyAreaId, table.countyValid], name: "district_county_pkey"}),
]);

export const election = pgTable("election", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	polityId: uuid("polity_id").notNull().references(() => polity.id),
	parentId: uuid("parent_id"),
	kind: electionKind().notNull(),
	dateFrom: date("date_from"),
	dateTo: date("date_to"),
	datePrecision: datePrecision("date_precision").default("day").notNull(),
	originalCalendar: calendar("original_calendar"),
	originalText: text("original_text"),
	status: electionStatus().notNull(),
	rerunOfId: uuid("rerun_of_id"),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.parentId],
		foreignColumns: [table.id],
		name: "election_parent_id_fkey"
	}),
	foreignKey({
		columns: [table.rerunOfId],
		foreignColumns: [table.id],
		name: "election_rerun_of_id_fkey"
	}),
	index("election_parent_idx").using("btree", table.parentId.asc().nullsLast()),
	index("election_polity_idx").using("btree", table.polityId.asc().nullsLast(), table.dateFrom.asc().nullsLast()),
check("election_check", sql`(date_to >= date_from)`),]);

export const electoralSystem = pgTable("electoral_system", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	polityId: uuid("polity_id").notNull().references(() => polity.id),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	rules: jsonb().notNull(),
	rulesVersion: integer("rules_version").notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const entity = pgTable("entity", {
	id: uuid().primaryKey(),
	type: entityType().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	createdAt: timestamp("created_at", { withTimezone: true }).default(sql`now()`).notNull(),
	retiredBy: uuid("retired_by").references(() => changeset.id),
}, (table) => [
	index("entity_retired_by_idx").using("btree", table.retiredBy.asc().nullsLast()).where(sql`(retired_by IS NOT NULL)`),
]);

export const entityMerge = pgTable("entity_merge", {
	entityId: uuid("entity_id").primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	intoId: uuid("into_id").notNull().references(() => entity.id, { onDelete: "cascade" } ),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	index("entity_merge_into_idx").using("btree", table.intoId.asc().nullsLast()),
check("entity_merge_check", sql`(entity_id <> into_id)`),]);

export const entityRetirement = pgTable("entity_retirement", {
	entityId: uuid("entity_id").primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
check("entity_retirement_reason_check", sql`(reason <> ''::text)`),]);

export const entitySubtypeDrop = pgTable("entity_subtype_drop", {
	entityId: uuid("entity_id").notNull().references(() => entity.id, { onDelete: "cascade" } ),
	subtype: text().notNull(),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.entityId, table.subtype], name: "entity_subtype_drop_pkey"}),
check("entity_subtype_drop_reason_check", sql`(reason <> ''::text)`),]);

export const externalId = pgTable("external_id", {
	entityId: uuid("entity_id").notNull().references(() => entity.id, { onDelete: "cascade" } ),
	scheme: text().notNull(),
	value: text().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.scheme, table.value], name: "external_id_pkey"}),
	index("external_id_entity_idx").using("btree", table.entityId.asc().nullsLast()),
]);

export const factRekey = pgTable("fact_rekey", {
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	relation: text().notNull(),
	keyColumn: text("key_column").notNull(),
	fromK: integer("from_k").notNull(),
	toK: integer("to_k").notNull(),
	rowKey: jsonb("row_key").notNull(),
}, (table) => [
	index("fact_rekey_changeset_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const financeSummary = pgTable("finance_summary", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	subjectEntityId: uuid("subject_entity_id").notNull().references(() => entity.id, { onDelete: "cascade" } ),
	electionId: uuid("election_id").references(() => election.id),
	measure: text().notNull(),
	value: numeric().notNull(),
	currency: char({ length: 3 }).notNull(),
	period: daterange(),
	sourceId: uuid("source_id").references(() => source.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const flag = pgTable("flag", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	entityId: uuid("entity_id").references(() => entity.id, { onDelete: "cascade" } ),
	resultSetId: uuid("result_set_id").references(() => resultSet.id, { onDelete: "cascade" } ),
	checkCode: text("check_code").notNull(),
	severity: flagSeverity().notNull(),
	details: jsonb(),
	status: flagStatus().default("open").notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	index("flag_entity_idx").using("btree", table.entityId.asc().nullsLast()),
	index("flag_result_set_idx").using("btree", table.resultSetId.asc().nullsLast()),
check("flag_check", sql`(num_nonnulls(entity_id, result_set_id) >= 1)`),]);

export const government = pgTable("government", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	polityId: uuid("polity_id").notNull().references(() => polity.id),
	officeId: uuid("office_id").references(() => office.id),
	headPersonId: uuid("head_person_id").references(() => person.id),
	fromDate: date("from_date").notNull(),
	fromPrecision: datePrecision("from_precision").default("day").notNull(),
	toDate: date("to_date"),
	toPrecision: datePrecision("to_precision").default("day").notNull(),
	electionId: uuid("election_id").references(() => election.id),
	previousGovernmentId: uuid("previous_government_id"),
	caretaker: boolean().default(false).notNull(),
	sourceId: uuid("source_id").references(() => source.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.previousGovernmentId],
		foreignColumns: [table.id],
		name: "government_previous_government_id_fkey"
	}),
	index("government_election_idx").using("btree", table.electionId.asc().nullsLast()),
	index("government_polity_idx").using("btree", table.polityId.asc().nullsLast(), table.fromDate.asc().nullsLast()),
check("government_check", sql`((to_date IS NULL) OR (to_date >= from_date))`),]);

export const governmentParty = pgTable("government_party", {
	governmentId: uuid("government_id").notNull().references(() => government.id, { onDelete: "cascade" } ),
	partyId: uuid("party_id").notNull().references(() => party.id),
	role: governmentPartyRole().notNull(),
	seats: integer(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.governmentId, table.partyId], name: "government_party_pkey"}),
	index("government_party_party_idx").using("btree", table.partyId.asc().nullsLast()),
check("government_party_seats_check", sql`(seats >= 0)`),]);

export const indicator = pgTable("indicator", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	code: text().notNull(),
	sourceId: uuid("source_id").references(() => source.id),
	vintage: text().notNull(),
	unit: text(),
	description: text(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	unique("indicator_code_key").on(table.code),]);

export const indicatorValue = pgTable("indicator_value", {
	indicatorId: uuid("indicator_id").notNull().references(() => indicator.id, { onDelete: "cascade" } ),
	areaK: integer("area_k").notNull(),
	value: numeric().notNull(),
}, (table) => [
	primaryKey({ columns: [table.indicatorId, table.areaK], name: "indicator_value_pkey"}),
]);

export const label = pgTable("label", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	entityId: uuid("entity_id").notNull().references(() => entity.id, { onDelete: "cascade" } ),
	kind: labelKind().notNull(),
	value: text().notNull(),
	assessor: text(),
	sourceId: uuid("source_id").references(() => source.id),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const measure = pgTable("measure", {
	id: smallint().primaryKey(),
	code: text().notNull(),
	description: text().notNull(),
}, (table) => [
	unique("measure_code_key").on(table.code),]);

export const membershipDrop = pgTable("membership_drop", {
	personId: uuid("person_id").notNull().references(() => person.id, { onDelete: "cascade" } ),
	partyId: uuid("party_id").notNull().references(() => party.id),
	validFrom: date("valid_from"),
	validTo: date("valid_to"),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	unique("membership_drop_key").on(table.personId, table.partyId, table.validFrom, table.validTo).nullsNotDistinct(),check("membership_drop_reason_check", sql`(reason <> ''::text)`),]);

export const name = pgTable("name", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	entityId: uuid("entity_id").notNull().references(() => entity.id, { onDelete: "cascade" } ),
	text: text().notNull(),
	lang: text().notNull(),
	script: text(),
	kind: nameKind().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	isPreferred: boolean("is_preferred").default(false).notNull(),
	sourceId: uuid("source_id").references(() => source.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	index("name_entity_idx").using("btree", table.entityId.asc().nullsLast()),
]);

export const nameDrop = pgTable("name_drop", {
	nameId: uuid("name_id").primaryKey(),
	entityId: uuid("entity_id").notNull().references(() => entity.id, { onDelete: "cascade" } ),
	text: text().notNull(),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
check("name_drop_reason_check", sql`(reason <> ''::text)`),]);

export const note = pgTable("note", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	entityId: uuid("entity_id").notNull().references(() => entity.id, { onDelete: "cascade" } ),
	kind: noteKind().notNull(),
	bodyMd: text("body_md").notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	index("note_entity_idx").using("btree", table.entityId.asc().nullsLast()),
]);

export const noteDrop = pgTable("note_drop", {
	noteId: uuid("note_id").primaryKey(),
	entityId: uuid("entity_id").notNull().references(() => entity.id, { onDelete: "cascade" } ),
	bodyMd: text("body_md").notNull(),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
check("note_drop_reason_check", sql`(reason <> ''::text)`),]);

export const optionDrop = pgTable("option_drop", {
	optionId: uuid("option_id").primaryKey(),
	contestId: uuid("contest_id").notNull().references(() => contest.id, { onDelete: "cascade" } ),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
check("option_drop_reason_check", sql`(reason <> ''::text)`),]);

export const office = pgTable("office", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	polityId: uuid("polity_id").notNull().references(() => polity.id),
	bodyId: uuid("body_id").references(() => body.id),
	kind: officeKind().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const outcome = pgTable("outcome", {
	resultSetId: uuid("result_set_id").notNull().references(() => resultSet.id, { onDelete: "cascade" } ),
	optionId: uuid("option_id").notNull().references(() => contestOption.id),
	kind: outcomeKind().notNull(),
	seats: integer(),
	notes: text(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	candidacyId: uuid("candidacy_id").references(() => candidacy.id, { onDelete: "cascade" } ),
}, (table) => [
	index("outcome_candidacy_idx").using("btree", table.candidacyId.asc().nullsLast()).where(sql`(candidacy_id IS NOT NULL)`),
	index("outcome_option_idx").using("btree", table.optionId.asc().nullsLast()),
	unique("outcome_key").on(table.resultSetId, table.optionId, table.candidacyId, table.kind).nullsNotDistinct(),]);

export const party = pgTable("party", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	kind: partyKind().default("party").notNull(),
	polityId: uuid("polity_id").references(() => polity.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const partyAffiliation = pgTable("party_affiliation", {
	partyId: uuid("party_id").notNull().references(() => party.id, { onDelete: "cascade" } ),
	parentPartyId: uuid("parent_party_id").notNull().references(() => party.id),
	kind: partyAffiliationKind().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.partyId, table.kind, table.valid], name: "party_affiliation_pkey"}),
]);

export const partyColor = pgTable("party_color", {
	partyId: uuid("party_id").notNull().references(() => party.id, { onDelete: "cascade" } ),
	color: text().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	sourceId: uuid("source_id").references(() => source.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.partyId, table.valid], name: "party_color_pkey"}),
check("party_color_color_check", sql`(color ~ '^#[0-9a-f]{6}$'::text)`),]);

export const partyPage = pgTable("party_page", {
	partyId: uuid("party_id").primaryKey(),
	page: jsonb().notNull(),
	builtAt: timestamp("built_at", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const partyPosition = pgTable("party_position", {
	partyId: uuid("party_id").notNull().references(() => party.id, { onDelete: "cascade" } ),
	low: smallint().notNull(),
	high: smallint().notNull(),
	rank: smallint(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	sourceId: uuid("source_id").references(() => source.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.partyId, table.valid], name: "party_position_pkey"}),
check("party_position_check", sql`((high >= low) AND (high <= 3))`),check("party_position_low_check", sql`((low >= '-3'::integer) AND (low <= 3))`),]);

export const partyRelation = pgTable("party_relation", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	fromPartyId: uuid("from_party_id").notNull().references(() => party.id),
	toPartyId: uuid("to_party_id").references(() => party.id),
	kind: partyRelationKind().notNull(),
	date: date(),
	datePrecision: datePrecision("date_precision").default("day").notNull(),
	electionId: uuid("election_id").references(() => election.id),
	sourceId: uuid("source_id").references(() => source.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
check("party_relation_check", sql`((kind = 'closed_by_court'::party_relation_kind) = (to_party_id IS NULL))`),]);

export const person = pgTable("person", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	birthDate: date("birth_date"),
	birthPrecision: datePrecision("birth_precision"),
	deathDate: date("death_date"),
	deathPrecision: datePrecision("death_precision"),
	gender: text(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const personImage = pgTable("person_image", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	personId: uuid("person_id").notNull().references(() => person.id),
	path: text().notNull(),
	license: text().notNull(),
	artist: text(),
	sourceUrl: text("source_url").notNull(),
	sourceId: uuid("source_id").references(() => source.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	takenText: text("taken_text"),
	takenFrom: date("taken_from"),
	takenFromPrecision: datePrecision("taken_from_precision").default("unknown").notNull(),
	takenTo: date("taken_to"),
	takenToPrecision: datePrecision("taken_to_precision").default("unknown").notNull(),
	uploadedAt: timestamp("uploaded_at", { withTimezone: true }),
}, (table) => [
	index("person_image_person_idx").using("btree", table.personId.asc().nullsLast()),
]);

export const personPartyMembership = pgTable("person_party_membership", {
	personId: uuid("person_id").notNull().references(() => person.id, { onDelete: "cascade" } ),
	partyId: uuid("party_id").references(() => party.id),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	sourceId: uuid("source_id").references(() => source.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	index("person_party_membership_party_idx").using("btree", table.partyId.asc().nullsLast()),
	unique("person_party_membership_key").on(table.personId, table.partyId, table.valid).nullsNotDistinct(),]);

export const polity = pgTable("polity", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	kind: polityKind().notNull(),
	isoCode: text("iso_code"),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	unique("polity_iso_code_key").on(table.isoCode),]);

export const polityParent = pgTable("polity_parent", {
	polityId: uuid("polity_id").notNull().references(() => polity.id),
	parentId: uuid("parent_id").notNull().references(() => polity.id),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.polityId, table.valid], name: "polity_parent_pkey"}),
]);

export const polityPeriod = pgTable("polity_period", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	polityId: uuid("polity_id").notNull().references(() => polity.id),
	kind: polityPeriodKind().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	notes: text(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const polityRelation = pgTable("polity_relation", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	fromPolityId: uuid("from_polity_id").notNull().references(() => polity.id),
	toPolityId: uuid("to_polity_id").notNull().references(() => polity.id),
	kind: polityRelationKind().notNull(),
	date: date(),
	datePrecision: datePrecision("date_precision").default("day").notNull(),
	sourceId: uuid("source_id").references(() => source.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const polityTerritory = pgTable("polity_territory", {
	polityId: uuid("polity_id").notNull().references(() => polity.id),
	areaId: uuid("area_id").notNull().references(() => area.id),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.polityId, table.valid], name: "polity_territory_pkey"}),
]);

export const release = pgTable("release", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	calver: text().notNull(),
	watermarkChangesetId: uuid("watermark_changeset_id").notNull().references(() => changeset.id),
	createdAt: timestamp("created_at", { withTimezone: true }).default(sql`now()`).notNull(),
	doi: text(),
	manifest: jsonb(),
}, (table) => [
	unique("release_calver_key").on(table.calver),]);

export const reportingUnit = pgTable("reporting_unit", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	k: integer().generatedAlwaysAsIdentity(),
	polityId: uuid("polity_id").notNull().references(() => polity.id),
	schemeCode: text("scheme_code").notNull().references(() => scheme.code),
	code: text().notNull(),
	parentId: uuid("parent_id"),
	countedWithId: uuid("counted_with_id"),
	areaRelation: areaRelation("area_relation").notNull(),
	areaId: uuid("area_id").references(() => area.id),
	location: geometry(),
	kind: reportingUnitKind().default("standard").notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.countedWithId],
		foreignColumns: [table.id],
		name: "reporting_unit_counted_with_id_fkey"
	}),
	foreignKey({
		columns: [table.parentId],
		foreignColumns: [table.id],
		name: "reporting_unit_parent_id_fkey"
	}),
	index("reporting_unit_area_idx").using("btree", table.areaId.asc().nullsLast()),
	index("reporting_unit_counted_with_idx").using("btree", table.countedWithId.asc().nullsLast()),
	index("reporting_unit_parent_idx").using("btree", table.parentId.asc().nullsLast()),
	index("reporting_unit_polity_idx").using("btree", table.polityId.asc().nullsLast()),
	unique("reporting_unit_k_key").on(table.k),	unique("reporting_unit_scheme_code_code_valid_key").on(table.schemeCode, table.code, table.valid),check("reporting_unit_check", sql`((area_relation = 'unlocated'::area_relation) = (area_id IS NULL))`),]);

export const reportingUnitArea = pgTable("reporting_unit_area", {
	unitId: uuid("unit_id").notNull().references(() => reportingUnit.id, { onDelete: "cascade" } ),
	areaId: uuid("area_id").notNull().references(() => area.id),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	approximate: boolean().default(false).notNull(),
	partial: boolean().default(false).notNull(),
	method: unitAreaMethod(),
}, (table) => [
	primaryKey({ columns: [table.unitId, table.areaId, table.valid], name: "reporting_unit_area_pkey"}),
	index("reporting_unit_area_area_idx").using("btree", table.areaId.asc().nullsLast()),
]);

export const representationGap = pgTable("representation_gap", {
	bodyId: uuid("body_id").notNull().references(() => body.id, { onDelete: "cascade" } ),
	areaId: uuid("area_id").notNull().references(() => area.id),
	reason: representationGapReason().notNull(),
	validFrom: date("valid_from").notNull(),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.bodyId, table.areaId, table.valid], name: "representation_gap_pkey"}),
]);

export const resultFlow = pgTable("result_flow", {
	resultSetK: integer("result_set_k").notNull(),
	unitK: integer("unit_k").notNull(),
	fromOptionK: integer("from_option_k").notNull(),
	optionK: integer("option_k").notNull(),
	channel: smallint().default(0).notNull(),
	measure: smallint().notNull(),
	countRound: smallint("count_round").default(0).notNull(),
	flags: smallint().default(0).notNull(),
	value: numeric().notNull(),
}, (table) => [
	primaryKey({ columns: [table.resultSetK, table.unitK, table.fromOptionK, table.optionK, table.channel, table.measure, table.countRound], name: "result_flow_pkey"}),
]);

export const resultRollupSum = pgTable("result_rollup_sum", {
	resultSetK: integer("result_set_k").notNull(),
	areaK: integer("area_k").notNull(),
	optionK: integer("option_k").default(0).notNull(),
	channel: smallint().default(0).notNull(),
	measure: smallint().notNull(),
	countRound: smallint("count_round").default(0).notNull(),
	complete: boolean().notNull(),
	value: numeric().notNull(),
}, (table) => [
	primaryKey({ columns: [table.resultSetK, table.areaK, table.optionK, table.channel, table.measure, table.countRound], name: "result_rollup_sum_pkey"}),
]);

export const resultRollupUnit = pgTable("result_rollup_unit", {
	resultSetK: integer("result_set_k").notNull(),
	areaK: integer("area_k").notNull(),
	unitK: integer("unit_k").notNull(),
}, (table) => [
	primaryKey({ columns: [table.resultSetK, table.areaK], name: "result_rollup_unit_pkey"}),
]);

export const resultSet = pgTable("result_set", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	k: integer().generatedAlwaysAsIdentity(),
	electionId: uuid("election_id").notNull().references(() => election.id),
	contestId: uuid("contest_id").references(() => contest.id),
	stage: stage().notNull(),
	kind: resultKind().default("counted").notNull(),
	sourceId: uuid("source_id").notNull().references(() => source.id),
	asOf: timestamp("as_of", { withTimezone: true }),
	status: resultSetStatus().default("active").notNull(),
	isDefault: boolean("is_default").default(false).notNull(),
	supersedesId: uuid("supersedes_id"),
	completeness: numeric(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.supersedesId],
		foreignColumns: [table.id],
		name: "result_set_supersedes_id_fkey"
	}),
	index("result_set_contest_idx").using("btree", table.contestId.asc().nullsLast()),
	uniqueIndex("result_set_default_idx").using("btree", table.electionId.asc().nullsLast(), table.contestId.asc().nullsLast(), table.stage.asc().nullsLast()).where(sql`is_default`),
	index("result_set_supersedes_idx").using("btree", table.supersedesId.asc().nullsLast()),
	unique("result_set_k_key").on(table.k),]);

export const resultValue = pgTable("result_value", {
	resultSetK: integer("result_set_k").notNull(),
	unitK: integer("unit_k").notNull(),
	optionK: integer("option_k").default(0).notNull(),
	channel: smallint().default(0).notNull(),
	measure: smallint().notNull(),
	countRound: smallint("count_round").default(0).notNull(),
	flags: smallint().default(0).notNull(),
	value: numeric().notNull(),
}, (table) => [
	primaryKey({ columns: [table.resultSetK, table.unitK, table.optionK, table.channel, table.measure, table.countRound], name: "result_value_pkey"}),
]);

export const rollupCoverage = pgTable("rollup_coverage", {
	resultSetK: integer("result_set_k").notNull(),
	areaK: integer("area_k").notNull(),
	reporting: integer().notNull(),
	expected: integer().notNull(),
}, (table) => [
	primaryKey({ columns: [table.resultSetK, table.areaK], name: "rollup_coverage_pkey"}),
]);

export const scheme = pgTable("scheme", {
	code: text().primaryKey(),
	polityId: uuid("polity_id").notNull().references(() => polity.id),
	kind: schemeKind().notNull(),
	rank: smallint().notNull(),
	noun: text().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
check("scheme_code_check", sql`(code ~ '^[a-z]{2,3}(-[a-z0-9]+)?\.[a-z0-9_]+$'::text)`),]);

export const searchDocument = pgTable("search_document", {
	entityId: uuid("entity_id").primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	type: entityType().notNull(),
	polityId: uuid("polity_id").references(() => polity.id),
	text: text().notNull(),
	tsv: customType({ dataType: () => 'tsvector' })().notNull(),
	rankBoost: real("rank_boost").default(0).notNull(),
}, (table) => [
	index("search_document_polity_idx").using("btree", table.polityId.asc().nullsLast()),
	index("search_document_trgm_idx").using("gin", table.text.asc().nullsLast().op("gin_trgm_ops")),
	index("search_document_tsv_idx").using("gin", table.tsv.asc().nullsLast()),
]);

export const seat = pgTable("seat", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	bodyId: uuid("body_id").notNull().references(() => body.id),
	districtAreaId: uuid("district_area_id").references(() => area.id),
	label: text(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	voting: boolean().default(true).notNull(),
	apportionedAreaId: uuid("apportioned_area_id").references(() => area.id),
});

export const slug = pgTable("slug", {
	path: text().primaryKey(),
	entityId: uuid("entity_id").notNull().references(() => entity.id, { onDelete: "cascade" } ),
	isCurrent: boolean("is_current").default(true).notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	uniqueIndex("slug_current_idx").using("btree", table.entityId.asc().nullsLast()).where(sql`is_current`),
	index("slug_entity_idx").using("btree", table.entityId.asc().nullsLast()),
]);

export const source = pgTable("source", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	kind: sourceKind().notNull(),
	publisher: text().notNull(),
	title: text().notNull(),
	url: text(),
	license: text(),
	licenseUrl: text("license_url"),
	attribution: text(),
	notes: text(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const sourceArtifact = pgTable("source_artifact", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	sourceId: uuid("source_id").notNull().references(() => source.id),
	url: text(),
	sha256: text().notNull(),
	retrievedAt: timestamp("retrieved_at", { withTimezone: true }).notNull(),
	storageKey: text("storage_key").notNull(),
	mediaType: text("media_type"),
	bytes: bigint({ mode: 'number' }),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const sourcePage = pgTable("source_page", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	artifactId: uuid("artifact_id").notNull().references(() => sourceArtifact.id),
	pageNumber: integer("page_number").notNull(),
	imageKey: text("image_key").notNull(),
	width: integer().notNull(),
	height: integer().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	unique("source_page_artifact_id_page_number_key").on(table.artifactId, table.pageNumber),]);

export const statewideOfficeYear = pgTable("statewide_office_year", {
	state: text().notNull(),
	officeId: uuid("office_id").notNull().references(() => office.id),
	class: text().notNull(),
	year: integer().notNull(),
	jointTicket: boolean("joint_ticket").notNull(),
	basis: text().notNull(),
}, (table) => [
	primaryKey({ columns: [table.officeId, table.year], name: "statewide_office_year_pkey"}),
	index("statewide_office_year_state_idx").using("btree", table.state.asc().nullsLast(), table.year.asc().nullsLast()),
]);

export const tenure = pgTable("tenure", {
	id: uuid().default(sql`uuidv7()`).primaryKey().references(() => entity.id, { onDelete: "cascade" } ),
	personId: uuid("person_id").notNull().references(() => person.id),
	officeId: uuid("office_id").references(() => office.id),
	seatId: uuid("seat_id").references(() => seat.id),
	bodyId: uuid("body_id").references(() => body.id),
	fromDate: date("from_date"),
	fromPrecision: datePrecision("from_precision").default("day").notNull(),
	toDate: date("to_date"),
	toPrecision: datePrecision("to_precision").default("day").notNull(),
	acquiredVia: acquiredVia("acquired_via").notNull(),
	contestId: uuid("contest_id").references(() => contest.id),
	endedVia: endedVia("ended_via"),
	resumesTenureId: uuid("resumes_tenure_id"),
	succeedsTenureId: uuid("succeeds_tenure_id"),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.resumesTenureId],
		foreignColumns: [table.id],
		name: "tenure_resumes_tenure_id_fkey"
	}),
	foreignKey({
		columns: [table.succeedsTenureId],
		foreignColumns: [table.id],
		name: "tenure_succeeds_tenure_id_fkey"
	}),
	index("tenure_contest_idx").using("btree", table.contestId.asc().nullsLast()),
	index("tenure_office_idx").using("btree", table.officeId.asc().nullsLast()),
	index("tenure_person_idx").using("btree", table.personId.asc().nullsLast()),
check("tenure_check", sql`(num_nonnulls(office_id, body_id) >= 1)`),]);

export const tenureAffiliation = pgTable("tenure_affiliation", {
	tenureId: uuid("tenure_id").notNull().references(() => tenure.id, { onDelete: "cascade" } ),
	kind: tenureAffiliationKind().default("party").notNull(),
	partyId: uuid("party_id").references(() => party.id),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	unique("tenure_affiliation_key").on(table.tenureId, table.kind, table.partyId, table.valid),]);

export const tenureEvent = pgTable("tenure_event", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	tenureId: uuid("tenure_id").notNull().references(() => tenure.id, { onDelete: "cascade" } ),
	kind: tenureEventKind().notNull(),
	date: date(),
	datePrecision: datePrecision("date_precision").default("day").notNull(),
	actor: text(),
	reason: text(),
	sourceId: uuid("source_id").references(() => source.id),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const unitAreaDrop = pgTable("unit_area_drop", {
	unitId: uuid("unit_id").notNull().references(() => reportingUnit.id, { onDelete: "cascade" } ),
	areaId: uuid("area_id").notNull().references(() => area.id),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull().references(() => changeset.id),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.unitId, table.areaId], name: "unit_area_drop_pkey"}),
check("unit_area_drop_reason_check", sql`(reason <> ''::text)`),]);

export const valueLocator = pgTable("value_locator", {
	resultSetK: integer("result_set_k").notNull(),
	unitK: integer("unit_k").notNull(),
	optionK: integer("option_k").default(0).notNull(),
	channel: smallint().default(0).notNull(),
	measure: smallint().notNull(),
	countRound: smallint("count_round").default(0).notNull(),
	pageId: uuid("page_id").references(() => sourcePage.id),
	sourceId: uuid("source_id").references(() => source.id),
	x0: real(),
	y0: real(),
	x1: real(),
	y1: real(),
}, (table) => [
	primaryKey({ columns: [table.resultSetK, table.unitK, table.optionK, table.channel, table.measure, table.countRound], name: "value_locator_pkey"}),
check("value_locator_check", sql`(num_nonnulls(page_id, source_id) >= 1)`),check("value_locator_check1", sql`((page_id IS NULL) = (x0 IS NULL))`),]);
export const resultRollup = pgView("result_rollup", {	resultSetK: integer("result_set_k"),
	areaK: integer("area_k"),
	optionK: integer("option_k"),
	channel: smallint(),
	measure: smallint(),
	countRound: smallint("count_round"),
	complete: boolean(),
	value: numeric(),
}).as(sql`SELECT result_rollup_sum.result_set_k, result_rollup_sum.area_k, result_rollup_sum.option_k, result_rollup_sum.channel, result_rollup_sum.measure, result_rollup_sum.count_round, result_rollup_sum.complete, result_rollup_sum.value FROM result_rollup_sum UNION ALL SELECT u.result_set_k, u.area_k, v.option_k, v.channel, v.measure, v.count_round, true AS complete, v.value FROM result_rollup_unit u JOIN result_value v ON v.result_set_k = u.result_set_k AND v.unit_k = u.unit_k`);