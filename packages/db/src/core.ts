import { daterange } from "./range.ts";
import { activityKind, apikeys, archiveCaptureStatus, proposalJobKind, proposalJobStatus, proposalState, threadKind, threadStatus, uploadStatus, users, watchTarget } from "./schema.ts";
import { pgTable, pgEnum, uuid, text, smallint, integer, boolean, timestamp, date, jsonb, customType, numeric, real, json, char, bigint, geometry, index, uniqueIndex, foreignKey, primaryKey, unique, check, pgView } from "drizzle-orm/pg-core"
import { sql } from "drizzle-orm"

export const acquiredVia = pgEnum("acquired_via", ["elected", "appointed", "trustee", "succession", "list_replacement", "co_opted", "acting", "ex_officio", "indirect"])
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
export const changesetKind = pgEnum("changeset_kind", ["import", "edit", "correction", "match", "digitization", "live", "release"])
export const changesetStatus = pgEnum("changeset_status", ["draft", "pending_review", "published", "rejected", "superseded"])
export const contestKind = pgEnum("contest_kind", ["office", "body_seats", "list_allocation", "ballot_question", "elector_slate", "delegate_allocation"])
export const contestTerm = pgEnum("contest_term", ["full", "remainder"])
export const crosswalkFromKind = pgEnum("crosswalk_from_kind", ["boundary_set", "reporting_scheme"])
export const crosswalkMethod = pgEnum("crosswalk_method", ["exact", "registered_voters", "population", "area"])
export const datePrecision = pgEnum("date_precision", ["day", "month", "year", "unknown"])
export const electionKind = pgEnum("election_kind", ["general", "by_election", "primary", "caucus", "runoff", "referendum", "recall", "indirect", "local", "special"])
export const electionStatus = pgEnum("election_status", ["scheduled", "held", "postponed", "canceled", "annulled"])
export const endedVia = pgEnum("ended_via", ["term_end", "resigned", "died", "removed", "annulled", "dissolved", "expelled", "lost_seat", "disqualified", "withdrew", "elevated", "appointed", "chose_another_seat"])
export const entityType = pgEnum("entity_type", ["polity", "area", "reporting_unit", "election", "contest", "contest_option", "result_set", "electoral_system", "party", "alliance", "person", "candidacy", "body", "office", "seat", "tenure", "source", "saved_view", "government"])
export const flagSeverity = pgEnum("flag_severity", ["info", "warning", "error"])
export const flagStatus = pgEnum("flag_status", ["open", "accepted", "resolved"])
export const geometryQuality = pgEnum("geometry_quality", ["official", "osm", "academic", "digitized", "synthetic"])
export const governmentPartyRole = pgEnum("government_party_role", ["head", "member", "support"])
export const labelKind = pgEnum("label_kind", ["recognition", "integrity", "data_quality", "ballot"])
export const nameKind = pgEnum("name_kind", ["official", "native", "english", "historical", "transliteration", "abbreviation", "ballot", "short"])
export const nominationKind = pgEnum("nomination_kind", ["nominated", "endorsed", "supported"])
export const noteKind = pgEnum("note_kind", ["context", "caveat", "methodology", "editorial", "retired"])
export const officeKind = pgEnum("office_kind", ["head_of_state", "head_of_government", "governor", "mayor", "deputy_mayor", "muhtar", "speaker", "other"])
export const optionKind = pgEnum("option_kind", ["candidate", "ticket", "list", "legend", "answer", "write_in", "others", "none_of_the_above", "uncommitted", "independents"])
export const outcomeKind = pgEnum("outcome_kind", ["elected", "won", "advanced", "eliminated", "lost", "tied", "withdrew", "disqualified", "declined"])
export const partyAffiliationKind = pgEnum("party_affiliation_kind", ["ep_group", "federation", "international", "regional_branch", "coalition"])
export const partyKind = pgEnum("party_kind", ["party", "independent_group", "list_vehicle", "federation", "bloc", "nuance", "parliamentary_group", "caucus"])
export const partyRelationKind = pgEnum("party_relation_kind", ["merged_into", "split_from", "successor_of", "refounded_as", "absorbed_by", "closed_by_court", "list_vehicle_for", "closed_by_government"])
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
export const unitAreaMethod = pgEnum("unit_area_method", ["source_code", "city_universe", "shape_contained", "official_list", "point_in_polygon"])
export const translationOrigin = pgEnum("translation_origin", ["official", "human", "machine"])
export const seatCategory = pgEnum("seat_category", ["elected", "appointed", "ex_officio", "indirect"])
export const cadenceSelection = pgEnum("cadence_selection", ["elected", "appointed", "landowner", "town_meeting", "abolished"])
export const cadenceRunoff = pgEnum("cadence_runoff", ["none", "majority", "threshold", "instant"])
export const resultsPlatformAccess = pgEnum("results_platform_access", ["open", "browser", "blocked", "manual", "none"])


export const activity = pgTable("activity", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	at: timestamp({ precision: 3, withTimezone: true }).default(sql`now()`).notNull(),
	actorUserId: uuid("actor_user_id").references(() => users.id, { onDelete: "set null" } ),
	agentKeyId: uuid("agent_key_id").references(() => apikeys.id, { onDelete: "set null" } ),
	kind: activityKind().notNull(),
	proposalId: uuid("proposal_id").references(() => proposal.id, { onDelete: "cascade" } ),
	changesetId: uuid("changeset_id").references(() => changeset.id, { onDelete: "set null" } ),
	entityIds: uuid("entity_ids").array().default([]).notNull(),
	polityIds: uuid("polity_ids").array().default([]).notNull(),
	summary: text().notNull(),
	threadId: uuid("thread_id"),
	commentId: uuid("comment_id"),
	notifyUserIds: uuid("notify_user_ids").array().default([]).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.commentId],
		foreignColumns: [comment.id],
		name: "activity_comment_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.threadId],
		foreignColumns: [thread.id],
		name: "activity_thread_id_fkey"
	}).onDelete("cascade"),
	index("activity_actor_user_id_at_idx").using("btree", table.actorUserId.asc().nullsLast(), table.at.desc().nullsLast()),
	index("activity_at_idx").using("btree", table.at.desc().nullsLast()),
	index("activity_entity_ids_idx").using("gin", table.entityIds.asc().nullsLast()),
	index("activity_notify_user_ids_idx").using("gin", table.notifyUserIds.asc().nullsLast()),
	index("activity_polity_ids_idx").using("gin", table.polityIds.asc().nullsLast()),
	index("activity_proposal_id_idx").using("btree", table.proposalId.asc().nullsLast()),
	index("activity_thread_id_idx").using("btree", table.threadId.asc().nullsLast()),
]);

export const alliance = pgTable("alliance", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	kind: allianceKind().notNull(),
	electionId: uuid("election_id"),
	parentAllianceId: uuid("parent_alliance_id"),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "alliance_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.electionId],
		foreignColumns: [election.id],
		name: "alliance_election_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "alliance_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.parentAllianceId],
		foreignColumns: [table.id],
		name: "alliance_parent_alliance_id_fkey"
	}),
	index("alliance_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const allianceMember = pgTable("alliance_member", {
	allianceId: uuid("alliance_id").notNull(),
	partyId: uuid("party_id").notNull(),
	role: text(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.allianceId, table.partyId], name: "alliance_member_pkey"}),
	foreignKey({
		columns: [table.allianceId],
		foreignColumns: [alliance.id],
		name: "alliance_member_alliance_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "alliance_member_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.partyId],
		foreignColumns: [party.id],
		name: "alliance_member_party_id_fkey"
	}),
	index("alliance_member_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const archiveCapture = pgTable("archive_capture", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	ownerUserId: uuid("owner_user_id"),
	url: text().notNull(),
	finalUrl: text("final_url"),
	waybackUrl: text("wayback_url"),
	status: archiveCaptureStatus().notNull(),
	error: text(),
	rawSha256: text("raw_sha256"),
	rawKey: text("raw_key"),
	mediaType: text("media_type"),
	bytes: bigint({ mode: 'number' }),
	capturedAt: timestamp("captured_at", { precision: 3, withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.ownerUserId],
		foreignColumns: [users.id],
		name: "archive_capture_owner_user_id_fkey"
	}).onDelete("set null"),
	index("archive_capture_owner_idx").using("btree", table.ownerUserId.asc().nullsLast(), table.capturedAt.desc().nullsFirst()),
	index("archive_capture_sha256_idx").using("btree", table.rawSha256.asc().nullsLast()),
check("archive_capture_raw_check", sql`((status = 'captured'::archive_capture_status) = ((raw_sha256 IS NOT NULL) AND (raw_key IS NOT NULL)))`),check("archive_capture_sha256_check", sql`(raw_sha256 ~ '^[0-9a-f]{64}$'::text)`),]);

export const area = pgTable("area", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	k: integer().generatedAlwaysAsIdentity(),
	schemeCode: text("scheme_code").notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "area_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "area_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.schemeCode],
		foreignColumns: [scheme.code],
		name: "area_scheme_code_fkey"
	}),
	index("area_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	unique("area_k_key").on(table.k),]);

export const areaCode = pgTable("area_code", {
	areaId: uuid("area_id").notNull(),
	code: text().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.areaId, table.valid], name: "area_code_pkey"}),
	foreignKey({
		columns: [table.areaId],
		foreignColumns: [area.id],
		name: "area_code_area_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "area_code_changeset_id_fkey"
	}),
	index("area_code_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("area_code_code_idx").using("btree", table.code.asc().nullsLast()),
]);

export const areaEvent = pgTable("area_event", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	kind: areaEventKind().notNull(),
	date: date(),
	datePrecision: datePrecision("date_precision").default("day").notNull(),
	sourceId: uuid("source_id"),
	notes: text(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "area_event_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "area_event_source_id_fkey"
	}),
	index("area_event_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const areaEventMember = pgTable("area_event_member", {
	eventId: uuid("event_id").notNull(),
	areaId: uuid("area_id").notNull(),
	role: areaEventRole().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.eventId, table.areaId, table.role], name: "area_event_member_pkey"}),
	foreignKey({
		columns: [table.areaId],
		foreignColumns: [area.id],
		name: "area_event_member_area_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "area_event_member_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.eventId],
		foreignColumns: [areaEvent.id],
		name: "area_event_member_event_id_fkey"
	}).onDelete("cascade"),
	index("area_event_member_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const areaGeometry = pgTable("area_geometry", {
	areaId: uuid("area_id").notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	geom: customType({ dataType: () => 'geometry(MultiPolygon,4326)' })().notNull(),
	labelPoint: geometry("label_point"),
	quality: geometryQuality().notNull(),
	sourceId: uuid("source_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.areaId, table.valid], name: "area_geometry_pkey"}),
	foreignKey({
		columns: [table.areaId],
		foreignColumns: [area.id],
		name: "area_geometry_area_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "area_geometry_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "area_geometry_source_id_fkey"
	}),
	index("area_geometry_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("area_geometry_geom_idx").using("gist", table.geom.asc().nullsLast()),
	index("area_geometry_synthetic_idx").using("btree", table.areaId.asc().nullsLast()).where(sql`(quality = 'synthetic'::geometry_quality)`),
]);

export const areaParent = pgTable("area_parent", {
	areaId: uuid("area_id").notNull(),
	parentId: uuid("parent_id").notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.areaId, table.valid], name: "area_parent_pkey"}),
	foreignKey({
		columns: [table.areaId],
		foreignColumns: [area.id],
		name: "area_parent_area_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "area_parent_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.parentId],
		foreignColumns: [area.id],
		name: "area_parent_parent_id_fkey"
	}),
	index("area_parent_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("area_parent_parent_idx").using("btree", table.parentId.asc().nullsLast()),
]);

export const areaUniverse = pgTable("area_universe", {
	areaId: uuid("area_id").notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	expected: integer().notNull(),
	method: unitAreaMethod().notNull(),
	sourceId: uuid("source_id"),
	resultSetId: uuid("result_set_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	shaped: boolean().notNull(),
}, (table) => [
	primaryKey({ columns: [table.areaId, table.shaped, table.valid], name: "area_universe_pkey"}),
	foreignKey({
		columns: [table.areaId],
		foreignColumns: [area.id],
		name: "area_universe_area_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "area_universe_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.resultSetId],
		foreignColumns: [resultSet.id],
		name: "area_universe_result_set_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "area_universe_source_id_fkey"
	}),
	index("area_universe_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("area_universe_check", sql`((method = 'city_universe'::unit_area_method) = (result_set_id IS NOT NULL))`),check("area_universe_expected_check", sql`(expected > 0)`),]);

export const ballotQuestion = pgTable("ballot_question", {
	contestId: uuid("contest_id").primaryKey(),
	type: ballotQuestionType().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "ballot_question_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.contestId],
		foreignColumns: [contest.id],
		name: "ballot_question_contest_id_fkey"
	}).onDelete("cascade"),
	index("ballot_question_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const ballotQuestionText = pgTable("ballot_question_text", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	contestId: uuid("contest_id").notNull(),
	kind: ballotQuestionTextKind().notNull(),
	lang: text().default("en").notNull(),
	body: text(),
	url: text(),
	sourceId: uuid("source_id").notNull(),
	locator: jsonb(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "ballot_question_text_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.contestId],
		foreignColumns: [ballotQuestion.contestId],
		name: "ballot_question_text_contest_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "ballot_question_text_source_id_fkey"
	}),
	index("ballot_question_text_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("ballot_question_text_source_idx").using("btree", table.sourceId.asc().nullsLast()),
	uniqueIndex("ballot_question_text_value_idx").using("btree", table.contestId.asc().nullsLast(), table.kind.asc().nullsLast(), table.lang.asc().nullsLast(), table.sourceId.asc().nullsLast(), table.url.asc().nullsLast()),
check("ballot_question_text_check", sql`((body IS NOT NULL) OR (url IS NOT NULL))`),check("ballot_question_text_check1", sql`((kind = 'link'::ballot_question_text_kind) = (body IS NULL))`),]);

export const body = pgTable("body", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	polityId: uuid("polity_id").notNull(),
	kind: bodyKind().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "body_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "body_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "body_polity_id_fkey"
	}),
	index("body_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("body_polity_idx").using("btree", table.polityId.asc().nullsLast()),
]);

export const bodyApportionment = pgTable("body_apportionment", {
	bodyId: uuid("body_id").notNull(),
	areaId: uuid("area_id").notNull(),
	seats: integer().notNull(),
	validFrom: date("valid_from").notNull(),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.bodyId, table.areaId, table.valid], name: "body_apportionment_pkey"}),
	foreignKey({
		columns: [table.areaId],
		foreignColumns: [area.id],
		name: "body_apportionment_area_id_fkey"
	}),
	foreignKey({
		columns: [table.bodyId],
		foreignColumns: [body.id],
		name: "body_apportionment_body_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "body_apportionment_changeset_id_fkey"
	}),
	index("body_apportionment_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const bodyChamber = pgTable("body_chamber", {
	bodyId: uuid("body_id").notNull(),
	ofBodyId: uuid("of_body_id").notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	sourceId: uuid("source_id").notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.bodyId, table.valid], name: "body_chamber_pkey"}),
	foreignKey({
		columns: [table.bodyId],
		foreignColumns: [body.id],
		name: "body_chamber_body_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "body_chamber_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.ofBodyId],
		foreignColumns: [body.id],
		name: "body_chamber_of_body_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "body_chamber_source_id_fkey"
	}),
	index("body_chamber_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const bodyEvent = pgTable("body_event", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	bodyId: uuid("body_id").notNull(),
	kind: bodyEventKind().notNull(),
	date: date(),
	datePrecision: datePrecision("date_precision").default("day").notNull(),
	sourceId: uuid("source_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.bodyId],
		foreignColumns: [body.id],
		name: "body_event_body_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "body_event_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "body_event_source_id_fkey"
	}),
	index("body_event_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const bodySize = pgTable("body_size", {
	bodyId: uuid("body_id").notNull(),
	seats: integer().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.bodyId, table.valid], name: "body_size_pkey"}),
	foreignKey({
		columns: [table.bodyId],
		foreignColumns: [body.id],
		name: "body_size_body_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "body_size_changeset_id_fkey"
	}),
	index("body_size_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const bodySizePart = pgTable("body_size_part", {
	bodyId: uuid("body_id").notNull(),
	category: seatCategory().notNull(),
	seats: integer().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.bodyId, table.category, table.valid], name: "body_size_part_pkey"}),
	foreignKey({
		columns: [table.bodyId],
		foreignColumns: [body.id],
		name: "body_size_part_body_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "body_size_part_changeset_id_fkey"
	}),
	index("body_size_part_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const boundarySet = pgTable("boundary_set", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	schemeCode: text("scheme_code").notNull(),
	vintage: text().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	asOf: date("as_of").notNull(),
	tilesKey: text("tiles_key"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "boundary_set_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.schemeCode],
		foreignColumns: [scheme.code],
		name: "boundary_set_scheme_code_fkey"
	}),
	index("boundary_set_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	unique("boundary_set_scheme_code_vintage_key").on(table.schemeCode, table.vintage),]);

export const candidacy = pgTable("candidacy", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	personId: uuid("person_id").notNull(),
	optionId: uuid("option_id").notNull(),
	role: candidacyRole().default("head").notNull(),
	listPosition: integer("list_position"),
	ballotName: text("ballot_name"),
	memberOfPartyId: uuid("member_of_party_id"),
	isIndependent: boolean("is_independent").default(false).notNull(),
	isIncumbent: boolean("is_incumbent"),
	ballotLines: smallint("ballot_lines").default(1).notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "candidacy_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "candidacy_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.memberOfPartyId],
		foreignColumns: [party.id],
		name: "candidacy_member_of_party_id_fkey"
	}),
	foreignKey({
		columns: [table.optionId],
		foreignColumns: [contestOption.id],
		name: "candidacy_option_id_fkey"
	}),
	foreignKey({
		columns: [table.personId],
		foreignColumns: [person.id],
		name: "candidacy_person_id_fkey"
	}),
	index("candidacy_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("candidacy_option_idx").using("btree", table.optionId.asc().nullsLast()),
	index("candidacy_person_idx").using("btree", table.personId.asc().nullsLast()),
check("candidacy_ballot_lines_check", sql`((ballot_lines >= 1) AND (ballot_lines <= 3))`),]);

export const candidacyNomination = pgTable("candidacy_nomination", {
	candidacyId: uuid("candidacy_id").notNull(),
	partyId: uuid("party_id").notNull(),
	kind: nominationKind().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.candidacyId, table.partyId], name: "candidacy_nomination_pkey"}),
	foreignKey({
		columns: [table.candidacyId],
		foreignColumns: [candidacy.id],
		name: "candidacy_nomination_candidacy_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "candidacy_nomination_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.partyId],
		foreignColumns: [party.id],
		name: "candidacy_nomination_party_id_fkey"
	}),
	index("candidacy_nomination_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
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
	entityId: uuid("entity_id").notNull(),
	sourceId: uuid("source_id").notNull(),
	locator: jsonb(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "citation_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "citation_entity_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "citation_source_id_fkey"
	}),
	index("citation_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("citation_entity_idx").using("btree", table.entityId.asc().nullsLast()),
]);

export const comment = pgTable("comment", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	threadId: uuid("thread_id").notNull(),
	parentId: uuid("parent_id"),
	authorUserId: uuid("author_user_id"),
	agentKeyId: uuid("agent_key_id"),
	agentName: text("agent_name"),
	agentModel: text("agent_model"),
	revisionN: integer("revision_n"),
	bodyMd: text("body_md").notNull(),
	anchor: jsonb(),
	suggestion: jsonb(),
	acceptedRevision: integer("accepted_revision"),
	createdAt: timestamp("created_at", { precision: 3, withTimezone: true }).default(sql`now()`).notNull(),
	editedAt: timestamp("edited_at", { precision: 3, withTimezone: true }),
	hiddenBy: uuid("hidden_by"),
	hiddenAt: timestamp("hidden_at", { precision: 3, withTimezone: true }),
	hiddenReason: text("hidden_reason"),
}, (table) => [
	foreignKey({
		columns: [table.agentKeyId],
		foreignColumns: [apikeys.id],
		name: "comment_agent_key_id_fkey"
	}).onDelete("set null"),
	foreignKey({
		columns: [table.authorUserId],
		foreignColumns: [users.id],
		name: "comment_author_user_id_fkey"
	}).onDelete("set null"),
	foreignKey({
		columns: [table.hiddenBy],
		foreignColumns: [users.id],
		name: "comment_hidden_by_fkey"
	}).onDelete("set null"),
	foreignKey({
		columns: [table.parentId],
		foreignColumns: [table.id],
		name: "comment_parent_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.threadId],
		foreignColumns: [thread.id],
		name: "comment_thread_id_fkey"
	}).onDelete("cascade"),
	index("comment_author_user_id_idx").using("btree", table.authorUserId.asc().nullsLast(), table.createdAt.desc().nullsFirst()),
	index("comment_thread_id_idx").using("btree", table.threadId.asc().nullsLast(), table.createdAt.asc().nullsLast()),
check("comment_body_check", sql`((char_length(body_md) >= 1) AND (char_length(body_md) <= 10000))`),check("comment_hidden_check", sql`((hidden_at IS NULL) = (hidden_reason IS NULL))`),]);

export const contest = pgTable("contest", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	electionId: uuid("election_id").notNull(),
	kind: contestKind().notNull(),
	officeId: uuid("office_id"),
	bodyId: uuid("body_id"),
	districtAreaId: uuid("district_area_id"),
	seatId: uuid("seat_id"),
	boundarySetId: uuid("boundary_set_id"),
	magnitude: integer(),
	partyScopeId: uuid("party_scope_id"),
	roundNumber: smallint("round_number").default(1).notNull(),
	previousRoundId: uuid("previous_round_id"),
	allocationParentId: uuid("allocation_parent_id"),
	electoralSystemId: uuid("electoral_system_id"),
	rulesOverride: jsonb("rules_override"),
	defaultResultKind: resultKind("default_result_kind").default("counted").notNull(),
	changesetId: uuid("changeset_id").notNull(),
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
		columns: [table.bodyId],
		foreignColumns: [body.id],
		name: "contest_body_id_fkey"
	}),
	foreignKey({
		columns: [table.boundarySetId],
		foreignColumns: [boundarySet.id],
		name: "contest_boundary_set_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "contest_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.districtAreaId],
		foreignColumns: [area.id],
		name: "contest_district_area_id_fkey"
	}),
	foreignKey({
		columns: [table.electionId],
		foreignColumns: [election.id],
		name: "contest_election_id_fkey"
	}),
	foreignKey({
		columns: [table.electoralSystemId],
		foreignColumns: [electoralSystem.id],
		name: "contest_electoral_system_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "contest_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.officeId],
		foreignColumns: [office.id],
		name: "contest_office_id_fkey"
	}),
	foreignKey({
		columns: [table.partyScopeId],
		foreignColumns: [party.id],
		name: "contest_party_scope_id_fkey"
	}),
	foreignKey({
		columns: [table.previousRoundId],
		foreignColumns: [table.id],
		name: "contest_previous_round_id_fkey"
	}),
	foreignKey({
		columns: [table.seatId],
		foreignColumns: [seat.id],
		name: "contest_seat_id_fkey"
	}),
	index("contest_allocation_parent_idx").using("btree", table.allocationParentId.asc().nullsLast()),
	index("contest_body_idx").using("btree", table.bodyId.asc().nullsLast()),
	index("contest_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("contest_district_area_idx").using("btree", table.districtAreaId.asc().nullsLast()),
	index("contest_election_idx").using("btree", table.electionId.asc().nullsLast()),
	index("contest_later_round_idx").using("btree", table.electionId.asc().nullsLast()).where(sql`(round_number > 1)`),
	index("contest_office_idx").using("btree", table.officeId.asc().nullsLast()),
	index("contest_previous_round_idx").using("btree", table.previousRoundId.asc().nullsLast()),
	index("contest_seat_idx").using("btree", table.seatId.asc().nullsLast()),
]);

export const contestOption = pgTable("contest_option", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	k: integer().generatedAlwaysAsIdentity(),
	contestId: uuid("contest_id").notNull(),
	kind: optionKind().notNull(),
	parentOptionId: uuid("parent_option_id"),
	partyId: uuid("party_id"),
	allianceId: uuid("alliance_id"),
	ballotPosition: integer("ballot_position"),
	ballotLabel: text("ballot_label"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	candidateCount: integer("candidate_count"),
}, (table) => [
	foreignKey({
		columns: [table.allianceId],
		foreignColumns: [alliance.id],
		name: "contest_option_alliance_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "contest_option_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.contestId],
		foreignColumns: [contest.id],
		name: "contest_option_contest_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "contest_option_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.parentOptionId],
		foreignColumns: [table.id],
		name: "contest_option_parent_option_id_fkey"
	}),
	foreignKey({
		columns: [table.partyId],
		foreignColumns: [party.id],
		name: "contest_option_party_id_fkey"
	}),
	index("contest_option_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("contest_option_contest_idx").using("btree", table.contestId.asc().nullsLast()),
	index("contest_option_parent_option_idx").using("btree", table.parentOptionId.asc().nullsLast()),
	index("contest_option_party_idx").using("btree", table.partyId.asc().nullsLast()),
	unique("contest_option_k_key").on(table.k),check("contest_option_candidate_count_check", sql`(candidate_count > 0)`),]);

export const coverageBuild = pgTable("coverage_build", {
	id: boolean().default(true).primaryKey(),
	requestedAt: timestamp("requested_at", { withTimezone: true }),
	startedAt: timestamp("started_at", { withTimezone: true }),
	builtAt: timestamp("built_at", { withTimezone: true }),
	commit: text(),
	inputs: jsonb(),
	latest: jsonb(),
}, (table) => [
check("coverage_build_id_check", sql`id`),]);

export const coverageCacheArea = pgTable("coverage_cache_area", {
	areaId: uuid("area_id").primaryKey(),
});

export const coverageCacheDone = pgTable("coverage_cache_done", {
	resultSetK: integer("result_set_k").primaryKey(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }),
});

export const coverageCacheMeasure = pgTable("coverage_cache_measure", {
	resultSetK: integer("result_set_k").notNull(),
	measure: smallint().notNull(),
}, (table) => [
	primaryKey({ columns: [table.resultSetK, table.measure], name: "coverage_cache_measure_pkey"}),
]);

export const coverageCachePart = pgTable("coverage_cache_part", {
	stage: text().notNull(),
	part: text().notNull(),
	inputs: text().notNull(),
	builtAt: timestamp("built_at", { withTimezone: true }).notNull(),
	seconds: real().notNull(),
	rows: json().notNull(),
}, (table) => [
	primaryKey({ columns: [table.stage, table.part], name: "coverage_cache_part_pkey"}),
]);

export const coverageCacheShapes = pgTable("coverage_cache_shapes", {
	key: text().primaryKey(),
	signature: text().notNull(),
	units: integer().notNull(),
	shaped: integer().notNull(),
});

export const coverageCacheUnit = pgTable("coverage_cache_unit", {
	resultSetK: integer("result_set_k").notNull(),
	unitK: integer("unit_k").notNull(),
}, (table) => [
	primaryKey({ columns: [table.resultSetK, table.unitK], name: "coverage_cache_unit_pkey"}),
]);

export const coverageCacheVoted = pgTable("coverage_cache_voted", {
	resultSetK: integer("result_set_k").primaryKey(),
	voted: boolean().notNull(),
});

export const coverageGoal = pgTable("coverage_goal", {
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
	polity: text().default("us").notNull(),
	populationTotal: bigint("population_total", { mode: 'number' }).default(0).notNull(),
	populationComplete: bigint("population_complete", { mode: 'number' }).default(0).notNull(),
	populationAbsent: bigint("population_absent", { mode: 'number' }).default(0).notNull(),
	populationEstimated: bigint("population_estimated", { mode: 'number' }).default(0).notNull(),
	major: integer().default(0).notNull(),
	published: integer().default(0).notNull(),
	knownGap: integer("known_gap").default(0).notNull(),
	upcoming: integer().default(0).notNull(),
	pending: integer().default(0).notNull(),
	blocked: integer().default(0).notNull(),
	inapplicable: integer().default(0).notNull(),
}, (table) => [
	primaryKey({ columns: [table.polity, table.goal, table.state, table.level, table.year], name: "coverage_goal_pkey"}),
]);

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
	populationBasis: text("population_basis"),
}, (table) => [
	index("coverage_government_polity_idx").using("btree", table.polityId.asc().nullsLast()),
	index("coverage_government_state_idx").using("btree", table.state.asc().nullsLast(), table.kind.asc().nullsLast()),
]);

export const coverageGovernmentYear = pgTable("coverage_government_year", {
	governmentId: text("government_id").notNull(),
	year: integer().notNull(),
	expected: boolean().notNull(),
	held: integer().notNull(),
	precinct: integer().notNull(),
	precincts: integer().notNull(),
	shaped: integer().notNull(),
	missing: boolean().notNull(),
}, (table) => [
	primaryKey({ columns: [table.year, table.governmentId], name: "coverage_government_year_pkey"}),
	foreignKey({
		columns: [table.governmentId],
		foreignColumns: [coverageGovernment.id],
		name: "coverage_government_year_government_id_fkey"
	}).onDelete("cascade"),
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

export const crosswalk = pgTable("crosswalk", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	fromKind: crosswalkFromKind("from_kind").notNull(),
	fromBoundarySetId: uuid("from_boundary_set_id"),
	fromSchemeCode: text("from_scheme_code"),
	fromAsOf: date("from_as_of"),
	toBoundarySetId: uuid("to_boundary_set_id").notNull(),
	method: crosswalkMethod().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "crosswalk_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.fromBoundarySetId],
		foreignColumns: [boundarySet.id],
		name: "crosswalk_from_boundary_set_id_fkey"
	}),
	foreignKey({
		columns: [table.fromSchemeCode],
		foreignColumns: [scheme.code],
		name: "crosswalk_from_scheme_code_fkey"
	}),
	foreignKey({
		columns: [table.toBoundarySetId],
		foreignColumns: [boundarySet.id],
		name: "crosswalk_to_boundary_set_id_fkey"
	}),
	index("crosswalk_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const crosswalkWeight = pgTable("crosswalk_weight", {
	crosswalkId: uuid("crosswalk_id").notNull(),
	fromK: integer("from_k").notNull(),
	toK: integer("to_k").notNull(),
	weight: numeric().notNull(),
	exact: boolean().notNull(),
}, (table) => [
	primaryKey({ columns: [table.crosswalkId, table.fromK, table.toK], name: "crosswalk_weight_pkey"}),
	foreignKey({
		columns: [table.crosswalkId],
		foreignColumns: [crosswalk.id],
		name: "crosswalk_weight_crosswalk_id_fkey"
	}).onDelete("cascade"),
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
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	polityId: uuid("polity_id").notNull(),
	parentId: uuid("parent_id"),
	kind: electionKind().notNull(),
	dateFrom: date("date_from"),
	dateTo: date("date_to"),
	datePrecision: datePrecision("date_precision").default("day").notNull(),
	originalCalendar: calendar("original_calendar"),
	originalText: text("original_text"),
	status: electionStatus().notNull(),
	rerunOfId: uuid("rerun_of_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "election_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "election_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.parentId],
		foreignColumns: [table.id],
		name: "election_parent_id_fkey"
	}),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "election_polity_id_fkey"
	}),
	foreignKey({
		columns: [table.rerunOfId],
		foreignColumns: [table.id],
		name: "election_rerun_of_id_fkey"
	}),
	index("election_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("election_parent_idx").using("btree", table.parentId.asc().nullsLast()),
	index("election_polity_idx").using("btree", table.polityId.asc().nullsLast(), table.dateFrom.asc().nullsLast()),
check("election_check", sql`(date_to >= date_from)`),]);

export const electionCadence = pgTable("election_cadence", {
	id: uuid().primaryKey(),
	polityId: uuid("polity_id").notNull(),
	governmentKind: text("government_kind"),
	function: text(),
	namePattern: text("name_pattern"),
	bodyId: uuid("body_id"),
	officeId: uuid("office_id"),
	selection: cadenceSelection().default("elected").notNull(),
	periodYears: integer("period_years"),
	phase: integer(),
	month: integer(),
	dayRule: text("day_rule"),
	classes: integer().default(1).notNull(),
	termYears: integer("term_years"),
	partisan: boolean(),
	runoff: cadenceRunoff().default("none").notNull(),
	runoffThreshold: numeric("runoff_threshold"),
	runoffRule: text("runoff_rule"),
	administeredBy: text("administered_by"),
	validFrom: date("valid_from"),
	validTo: date("valid_to"),
	sourceId: uuid("source_id").notNull(),
	locator: text().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.bodyId],
		foreignColumns: [body.id],
		name: "election_cadence_body_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "election_cadence_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.officeId],
		foreignColumns: [office.id],
		name: "election_cadence_office_id_fkey"
	}),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "election_cadence_polity_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "election_cadence_source_id_fkey"
	}),
	index("election_cadence_polity_idx").using("btree", table.polityId.asc().nullsLast(), table.governmentKind.asc().nullsLast()),
check("election_cadence_check", sql`((phase IS NULL) OR ((period_years IS NOT NULL) AND ((phase >= 0) AND (phase <= (period_years - 1)))))`),check("election_cadence_check1", sql`((government_kind IS NOT NULL) OR ((function IS NULL) AND (name_pattern IS NULL)))`),check("election_cadence_check2", sql`((valid_to IS NULL) OR (valid_from IS NULL) OR (valid_to > valid_from))`),check("election_cadence_classes_check", sql`(classes >= 1)`),check("election_cadence_month_check", sql`((month >= 1) AND (month <= 12))`),check("election_cadence_period_years_check", sql`((period_years >= 1) AND (period_years <= 8))`),]);

export const electoralSystem = pgTable("electoral_system", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	polityId: uuid("polity_id").notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	rules: jsonb().notNull(),
	rulesVersion: integer("rules_version").notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "electoral_system_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "electoral_system_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "electoral_system_polity_id_fkey"
	}),
	index("electoral_system_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const entity = pgTable("entity", {
	id: uuid().primaryKey(),
	type: entityType().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	createdAt: timestamp("created_at", { withTimezone: true }).default(sql`now()`).notNull(),
	retiredBy: uuid("retired_by"),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "entity_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.retiredBy],
		foreignColumns: [changeset.id],
		name: "entity_retired_by_fkey"
	}),
	index("entity_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("entity_retired_by_idx").using("btree", table.retiredBy.asc().nullsLast()).where(sql`(retired_by IS NOT NULL)`),
]);

export const entityMerge = pgTable("entity_merge", {
	entityId: uuid("entity_id").primaryKey(),
	intoId: uuid("into_id").notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "entity_merge_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "entity_merge_entity_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.intoId],
		foreignColumns: [entity.id],
		name: "entity_merge_into_id_fkey"
	}).onDelete("cascade"),
	index("entity_merge_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("entity_merge_into_idx").using("btree", table.intoId.asc().nullsLast()),
check("entity_merge_check", sql`(entity_id <> into_id)`),]);

export const entityRetirement = pgTable("entity_retirement", {
	entityId: uuid("entity_id").primaryKey(),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "entity_retirement_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "entity_retirement_entity_id_fkey"
	}).onDelete("cascade"),
	index("entity_retirement_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("entity_retirement_reason_check", sql`(reason <> ''::text)`),]);

export const entitySubtypeDrop = pgTable("entity_subtype_drop", {
	entityId: uuid("entity_id").notNull(),
	subtype: text().notNull(),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.entityId, table.subtype], name: "entity_subtype_drop_pkey"}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "entity_subtype_drop_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "entity_subtype_drop_entity_id_fkey"
	}).onDelete("cascade"),
	index("entity_subtype_drop_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("entity_subtype_drop_reason_check", sql`(reason <> ''::text)`),]);

export const externalId = pgTable("external_id", {
	entityId: uuid("entity_id").notNull(),
	scheme: text().notNull(),
	value: text().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.scheme, table.value], name: "external_id_pkey"}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "external_id_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "external_id_entity_id_fkey"
	}).onDelete("cascade"),
	index("external_id_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("external_id_entity_idx").using("btree", table.entityId.asc().nullsLast()),
]);

export const externalIdDrop = pgTable("external_id_drop", {
	entityId: uuid("entity_id").notNull(),
	scheme: text().notNull(),
	value: text().notNull(),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.scheme, table.value, table.changesetId], name: "external_id_drop_pkey"}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "external_id_drop_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "external_id_drop_entity_id_fkey"
	}).onDelete("cascade"),
	index("external_id_drop_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("external_id_drop_reason_check", sql`(reason <> ''::text)`),]);

export const factRekey = pgTable("fact_rekey", {
	changesetId: uuid("changeset_id").notNull(),
	relation: text().notNull(),
	keyColumn: text("key_column").notNull(),
	fromK: integer("from_k").notNull(),
	toK: integer("to_k").notNull(),
	rowKey: jsonb("row_key").notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "fact_rekey_changeset_id_fkey"
	}),
	index("fact_rekey_changeset_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const financeSummary = pgTable("finance_summary", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	subjectEntityId: uuid("subject_entity_id").notNull(),
	electionId: uuid("election_id"),
	measure: text().notNull(),
	value: numeric().notNull(),
	currency: char({ length: 3 }).notNull(),
	period: daterange(),
	sourceId: uuid("source_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "finance_summary_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.electionId],
		foreignColumns: [election.id],
		name: "finance_summary_election_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "finance_summary_source_id_fkey"
	}),
	foreignKey({
		columns: [table.subjectEntityId],
		foreignColumns: [entity.id],
		name: "finance_summary_subject_entity_id_fkey"
	}).onDelete("cascade"),
	index("finance_summary_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const flag = pgTable("flag", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	entityId: uuid("entity_id"),
	resultSetId: uuid("result_set_id"),
	checkCode: text("check_code").notNull(),
	severity: flagSeverity().notNull(),
	details: jsonb(),
	status: flagStatus().default("open").notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "flag_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "flag_entity_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.resultSetId],
		foreignColumns: [resultSet.id],
		name: "flag_result_set_id_fkey"
	}).onDelete("cascade"),
	index("flag_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("flag_entity_idx").using("btree", table.entityId.asc().nullsLast()),
	index("flag_result_set_idx").using("btree", table.resultSetId.asc().nullsLast()),
check("flag_check", sql`(num_nonnulls(entity_id, result_set_id) >= 1)`),]);

export const government = pgTable("government", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	polityId: uuid("polity_id").notNull(),
	officeId: uuid("office_id"),
	headPersonId: uuid("head_person_id"),
	fromDate: date("from_date").notNull(),
	fromPrecision: datePrecision("from_precision").default("day").notNull(),
	toDate: date("to_date"),
	toPrecision: datePrecision("to_precision").default("day").notNull(),
	electionId: uuid("election_id"),
	previousGovernmentId: uuid("previous_government_id"),
	caretaker: boolean().default(false).notNull(),
	sourceId: uuid("source_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "government_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.electionId],
		foreignColumns: [election.id],
		name: "government_election_id_fkey"
	}),
	foreignKey({
		columns: [table.headPersonId],
		foreignColumns: [person.id],
		name: "government_head_person_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "government_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.officeId],
		foreignColumns: [office.id],
		name: "government_office_id_fkey"
	}),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "government_polity_id_fkey"
	}),
	foreignKey({
		columns: [table.previousGovernmentId],
		foreignColumns: [table.id],
		name: "government_previous_government_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "government_source_id_fkey"
	}),
	index("government_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("government_election_idx").using("btree", table.electionId.asc().nullsLast()),
	index("government_polity_idx").using("btree", table.polityId.asc().nullsLast(), table.fromDate.asc().nullsLast()),
check("government_check", sql`((to_date IS NULL) OR (to_date >= from_date))`),]);

export const governmentParty = pgTable("government_party", {
	governmentId: uuid("government_id").notNull(),
	partyId: uuid("party_id").notNull(),
	role: governmentPartyRole().notNull(),
	seats: integer(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.governmentId, table.partyId], name: "government_party_pkey"}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "government_party_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.governmentId],
		foreignColumns: [government.id],
		name: "government_party_government_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.partyId],
		foreignColumns: [party.id],
		name: "government_party_party_id_fkey"
	}),
	index("government_party_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("government_party_party_idx").using("btree", table.partyId.asc().nullsLast()),
check("government_party_seats_check", sql`(seats >= 0)`),]);

export const indicator = pgTable("indicator", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	code: text().notNull(),
	sourceId: uuid("source_id"),
	vintage: text().notNull(),
	unit: text(),
	description: text(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "indicator_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "indicator_source_id_fkey"
	}),
	index("indicator_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	unique("indicator_code_key").on(table.code),]);

export const indicatorValue = pgTable("indicator_value", {
	indicatorId: uuid("indicator_id").notNull(),
	areaK: integer("area_k").notNull(),
	value: numeric().notNull(),
}, (table) => [
	primaryKey({ columns: [table.indicatorId, table.areaK], name: "indicator_value_pkey"}),
	foreignKey({
		columns: [table.indicatorId],
		foreignColumns: [indicator.id],
		name: "indicator_value_indicator_id_fkey"
	}).onDelete("cascade"),
]);

export const label = pgTable("label", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	entityId: uuid("entity_id").notNull(),
	kind: labelKind().notNull(),
	value: text().notNull(),
	assessor: text(),
	sourceId: uuid("source_id"),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "label_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "label_entity_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "label_source_id_fkey"
	}),
	index("label_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const measure = pgTable("measure", {
	id: smallint().primaryKey(),
	code: text().notNull(),
	description: text().notNull(),
}, (table) => [
	unique("measure_code_key").on(table.code),]);

export const membershipDrop = pgTable("membership_drop", {
	personId: uuid("person_id").notNull(),
	partyId: uuid("party_id").notNull(),
	validFrom: date("valid_from"),
	validTo: date("valid_to"),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "membership_drop_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.partyId],
		foreignColumns: [party.id],
		name: "membership_drop_party_id_fkey"
	}),
	foreignKey({
		columns: [table.personId],
		foreignColumns: [person.id],
		name: "membership_drop_person_id_fkey"
	}).onDelete("cascade"),
	index("membership_drop_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	unique("membership_drop_key").on(table.personId, table.partyId, table.validFrom, table.validTo).nullsNotDistinct(),check("membership_drop_reason_check", sql`(reason <> ''::text)`),]);

export const name = pgTable("name", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	entityId: uuid("entity_id").notNull(),
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
	sourceId: uuid("source_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "name_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "name_entity_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "name_source_id_fkey"
	}),
	index("name_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("name_entity_idx").using("btree", table.entityId.asc().nullsLast()),
]);

export const nameDrop = pgTable("name_drop", {
	nameId: uuid("name_id").primaryKey(),
	entityId: uuid("entity_id").notNull(),
	text: text().notNull(),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "name_drop_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "name_drop_entity_id_fkey"
	}).onDelete("cascade"),
	index("name_drop_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("name_drop_reason_check", sql`(reason <> ''::text)`),]);

export const note = pgTable("note", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	entityId: uuid("entity_id").notNull(),
	kind: noteKind().notNull(),
	bodyMd: text("body_md").notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	lang: text().default("en").notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "note_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "note_entity_id_fkey"
	}).onDelete("cascade"),
	index("note_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("note_entity_idx").using("btree", table.entityId.asc().nullsLast()),
check("note_body_nfc", sql`(body_md IS NFC NORMALIZED)`),check("note_lang_bcp47", sql`(lang ~ '^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$'::text)`),]);

export const noteDrop = pgTable("note_drop", {
	noteId: uuid("note_id").primaryKey(),
	entityId: uuid("entity_id").notNull(),
	bodyMd: text("body_md").notNull(),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "note_drop_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "note_drop_entity_id_fkey"
	}).onDelete("cascade"),
	index("note_drop_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("note_drop_reason_check", sql`(reason <> ''::text)`),]);

export const office = pgTable("office", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	polityId: uuid("polity_id").notNull(),
	bodyId: uuid("body_id"),
	kind: officeKind().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.bodyId],
		foreignColumns: [body.id],
		name: "office_body_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "office_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "office_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "office_polity_id_fkey"
	}),
	index("office_body_idx").using("btree", table.bodyId.asc().nullsLast()),
	index("office_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("office_polity_idx").using("btree", table.polityId.asc().nullsLast()),
]);

export const optionDrop = pgTable("option_drop", {
	optionId: uuid("option_id").primaryKey(),
	contestId: uuid("contest_id").notNull(),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "option_drop_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.contestId],
		foreignColumns: [contest.id],
		name: "option_drop_contest_id_fkey"
	}).onDelete("cascade"),
	index("option_drop_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("option_drop_reason_check", sql`(reason <> ''::text)`),]);

export const outcome = pgTable("outcome", {
	resultSetId: uuid("result_set_id").notNull(),
	optionId: uuid("option_id").notNull(),
	kind: outcomeKind().notNull(),
	seats: integer(),
	notes: text(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	candidacyId: uuid("candidacy_id"),
}, (table) => [
	foreignKey({
		columns: [table.candidacyId],
		foreignColumns: [candidacy.id],
		name: "outcome_candidacy_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "outcome_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.optionId],
		foreignColumns: [contestOption.id],
		name: "outcome_option_id_fkey"
	}),
	foreignKey({
		columns: [table.resultSetId],
		foreignColumns: [resultSet.id],
		name: "outcome_result_set_id_fkey"
	}).onDelete("cascade"),
	index("outcome_candidacy_idx").using("btree", table.candidacyId.asc().nullsLast()).where(sql`(candidacy_id IS NOT NULL)`),
	index("outcome_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("outcome_option_idx").using("btree", table.optionId.asc().nullsLast()),
	unique("outcome_key").on(table.resultSetId, table.optionId, table.candidacyId, table.kind).nullsNotDistinct(),]);

export const party = pgTable("party", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	kind: partyKind().default("party").notNull(),
	polityId: uuid("polity_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "party_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "party_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "party_polity_id_fkey"
	}),
	index("party_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const partyAffiliation = pgTable("party_affiliation", {
	partyId: uuid("party_id").notNull(),
	parentPartyId: uuid("parent_party_id").notNull(),
	kind: partyAffiliationKind().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.partyId, table.kind, table.valid], name: "party_affiliation_pkey"}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "party_affiliation_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.parentPartyId],
		foreignColumns: [party.id],
		name: "party_affiliation_parent_party_id_fkey"
	}),
	foreignKey({
		columns: [table.partyId],
		foreignColumns: [party.id],
		name: "party_affiliation_party_id_fkey"
	}).onDelete("cascade"),
	index("party_affiliation_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const partyColor = pgTable("party_color", {
	partyId: uuid("party_id").notNull(),
	color: text().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	sourceId: uuid("source_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.partyId, table.valid], name: "party_color_pkey"}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "party_color_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.partyId],
		foreignColumns: [party.id],
		name: "party_color_party_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "party_color_source_id_fkey"
	}),
	index("party_color_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("party_color_color_check", sql`(color ~ '^#[0-9a-f]{6}$'::text)`),]);

export const partyFounding = pgTable("party_founding", {
	partyId: uuid("party_id").primaryKey(),
	date: date().notNull(),
	datePrecision: datePrecision("date_precision").notNull(),
	sourceId: uuid("source_id").notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "party_founding_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.partyId],
		foreignColumns: [party.id],
		name: "party_founding_party_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "party_founding_source_id_fkey"
	}),
	index("party_founding_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const partyImage = pgTable("party_image", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	partyId: uuid("party_id").notNull(),
	path: text().notNull(),
	width: integer().notNull(),
	height: integer().notNull(),
	license: text().notNull(),
	artist: text(),
	sourceUrl: text("source_url").notNull(),
	sourceId: uuid("source_id"),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("unknown").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("unknown").notNull(),
	valid: daterange().generatedAlwaysAs(sql`
CASE
    WHEN ((valid_from IS NOT NULL) OR (valid_to IS NOT NULL)) THEN daterange(valid_from, valid_to, '[)'::text)
    ELSE NULL::daterange
END`),
	datedBy: text("dated_by"),
	uploadedAt: timestamp("uploaded_at", { withTimezone: true }),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	datedUrl: text("dated_url"),
	isLight: boolean("is_light").default(false).notNull(),
	markPath: text("mark_path"),
	shape: text(),
	isPlate: boolean("is_plate").default(false).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "party_image_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.partyId],
		foreignColumns: [party.id],
		name: "party_image_party_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "party_image_source_id_fkey"
	}),
	index("party_image_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("party_image_party_idx").using("btree", table.partyId.asc().nullsLast()),
check("party_image_check", sql`((dated_by IS NULL) = ((valid_from IS NULL) AND (valid_to IS NULL)))`),check("party_image_dated_by_check", sql`(dated_by = ANY (ARRAY['wikidata'::text, 'file'::text, 'source'::text, 'next_logo'::text, 'inception'::text]))`),check("party_image_dated_url_check", sql`((dated_by = 'source'::text) = (dated_url IS NOT NULL))`),check("party_image_height_check", sql`(height > 0)`),check("party_image_shape_check", sql`(shape = ANY (ARRAY['mark'::text, 'wordmark'::text]))`),check("party_image_width_check", sql`(width > 0)`),]);

export const partyPage = pgTable("party_page", {
	partyId: uuid("party_id").primaryKey(),
	page: jsonb().notNull(),
	builtAt: timestamp("built_at", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const partyPosition = pgTable("party_position", {
	partyId: uuid("party_id").notNull(),
	low: smallint().notNull(),
	high: smallint().notNull(),
	rank: smallint(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	sourceId: uuid("source_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.partyId, table.valid], name: "party_position_pkey"}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "party_position_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.partyId],
		foreignColumns: [party.id],
		name: "party_position_party_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "party_position_source_id_fkey"
	}),
	index("party_position_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("party_position_check", sql`((high >= low) AND (high <= 3))`),check("party_position_low_check", sql`((low >= '-3'::integer) AND (low <= 3))`),]);

export const partyRelation = pgTable("party_relation", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	fromPartyId: uuid("from_party_id").notNull(),
	toPartyId: uuid("to_party_id"),
	kind: partyRelationKind().notNull(),
	date: date(),
	datePrecision: datePrecision("date_precision").default("day").notNull(),
	electionId: uuid("election_id"),
	sourceId: uuid("source_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "party_relation_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.electionId],
		foreignColumns: [election.id],
		name: "party_relation_election_id_fkey"
	}),
	foreignKey({
		columns: [table.fromPartyId],
		foreignColumns: [party.id],
		name: "party_relation_from_party_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "party_relation_source_id_fkey"
	}),
	foreignKey({
		columns: [table.toPartyId],
		foreignColumns: [party.id],
		name: "party_relation_to_party_id_fkey"
	}),
	index("party_relation_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("party_relation_check", sql`(((kind)::text = ANY (ARRAY['closed_by_court'::text, 'closed_by_government'::text])) = (to_party_id IS NULL))`),]);

export const person = pgTable("person", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	birthDate: date("birth_date"),
	birthPrecision: datePrecision("birth_precision"),
	deathDate: date("death_date"),
	deathPrecision: datePrecision("death_precision"),
	gender: text(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "person_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "person_id_fkey"
	}).onDelete("cascade"),
	index("person_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const personImage = pgTable("person_image", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	personId: uuid("person_id").notNull(),
	path: text().notNull(),
	license: text().notNull(),
	artist: text(),
	sourceUrl: text("source_url").notNull(),
	sourceId: uuid("source_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	takenText: text("taken_text"),
	takenFrom: date("taken_from"),
	takenFromPrecision: datePrecision("taken_from_precision").default("unknown").notNull(),
	takenTo: date("taken_to"),
	takenToPrecision: datePrecision("taken_to_precision").default("unknown").notNull(),
	uploadedAt: timestamp("uploaded_at", { withTimezone: true }),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "person_image_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.personId],
		foreignColumns: [person.id],
		name: "person_image_person_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "person_image_source_id_fkey"
	}),
	index("person_image_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("person_image_person_idx").using("btree", table.personId.asc().nullsLast()),
]);

export const personPartyMembership = pgTable("person_party_membership", {
	personId: uuid("person_id").notNull(),
	partyId: uuid("party_id"),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	sourceId: uuid("source_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "person_party_membership_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.partyId],
		foreignColumns: [party.id],
		name: "person_party_membership_party_id_fkey"
	}),
	foreignKey({
		columns: [table.personId],
		foreignColumns: [person.id],
		name: "person_party_membership_person_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "person_party_membership_source_id_fkey"
	}),
	index("person_party_membership_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("person_party_membership_party_idx").using("btree", table.partyId.asc().nullsLast()),
	unique("person_party_membership_key").on(table.personId, table.partyId, table.valid).nullsNotDistinct(),]);

export const polity = pgTable("polity", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	kind: polityKind().notNull(),
	isoCode: text("iso_code"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "polity_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "polity_id_fkey"
	}).onDelete("cascade"),
	index("polity_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	unique("polity_iso_code_key").on(table.isoCode),]);

export const polityParent = pgTable("polity_parent", {
	polityId: uuid("polity_id").notNull(),
	parentId: uuid("parent_id").notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.polityId, table.valid], name: "polity_parent_pkey"}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "polity_parent_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.parentId],
		foreignColumns: [polity.id],
		name: "polity_parent_parent_id_fkey"
	}),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "polity_parent_polity_id_fkey"
	}),
	index("polity_parent_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("polity_parent_parent_idx").using("btree", table.parentId.asc().nullsLast()),
]);

export const polityPeriod = pgTable("polity_period", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	polityId: uuid("polity_id").notNull(),
	kind: polityPeriodKind().notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	notes: text(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "polity_period_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "polity_period_polity_id_fkey"
	}),
	index("polity_period_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const polityRecord = pgTable("polity_record", {
	polityId: uuid("polity_id").primaryKey(),
	record: jsonb().notNull(),
	builtAt: timestamp("built_at", { withTimezone: true }).default(sql`now()`).notNull(),
});

export const polityRelation = pgTable("polity_relation", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	fromPolityId: uuid("from_polity_id").notNull(),
	toPolityId: uuid("to_polity_id").notNull(),
	kind: polityRelationKind().notNull(),
	date: date(),
	datePrecision: datePrecision("date_precision").default("day").notNull(),
	sourceId: uuid("source_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "polity_relation_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.fromPolityId],
		foreignColumns: [polity.id],
		name: "polity_relation_from_polity_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "polity_relation_source_id_fkey"
	}),
	foreignKey({
		columns: [table.toPolityId],
		foreignColumns: [polity.id],
		name: "polity_relation_to_polity_id_fkey"
	}),
	index("polity_relation_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const polityTerritory = pgTable("polity_territory", {
	polityId: uuid("polity_id").notNull(),
	areaId: uuid("area_id").notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.polityId, table.valid], name: "polity_territory_pkey"}),
	foreignKey({
		columns: [table.areaId],
		foreignColumns: [area.id],
		name: "polity_territory_area_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "polity_territory_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "polity_territory_polity_id_fkey"
	}),
	index("polity_territory_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const profile = pgTable("profile", {
	userId: uuid("user_id").primaryKey().references(() => users.id, { onDelete: "cascade" } ),
	handle: text().notNull(),
	displayName: text("display_name"),
	bioMd: text("bio_md"),
	affiliation: text(),
	links: jsonb().default([]).notNull(),
	licenseAcceptedAt: timestamp("license_accepted_at", { precision: 3, withTimezone: true }),
	feedToken: text("feed_token"),
	createdAt: timestamp("created_at", { precision: 3, withTimezone: true }).default(sql`now()`).notNull(),
	digest: boolean().default(false).notNull(),
}, (table) => [
	unique("profile_feed_token_key").on(table.feedToken),	unique("profile_handle_key").on(table.handle),]);

export const proposal = pgTable("proposal", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	number: integer().generatedAlwaysAsIdentity({ startWith: 1000 }),
	authorUserId: uuid("author_user_id").references(() => users.id, { onDelete: "set null" } ),
	agentKeyId: uuid("agent_key_id").references(() => apikeys.id, { onDelete: "set null" } ),
	agentName: text("agent_name"),
	agentModel: text("agent_model"),
	title: text().notNull(),
	polityId: uuid("polity_id").references(() => polity.id, { onDelete: "set null" } ),
	state: proposalState().default("draft").notNull(),
	tier: smallint(),
	headChangesetId: uuid("head_changeset_id").references(() => changeset.id, { onDelete: "set null" } ),
	draft: jsonb(),
	prescreen: jsonb(),
	prereview: jsonb(),
	decidedBy: uuid("decided_by").references(() => users.id, { onDelete: "set null" } ),
	decidedAt: timestamp("decided_at", { precision: 3, withTimezone: true }),
	decisionReason: text("decision_reason"),
	createdAt: timestamp("created_at", { precision: 3, withTimezone: true }).default(sql`now()`).notNull(),
	updatedAt: timestamp("updated_at", { precision: 3, withTimezone: true }).default(sql`now()`).notNull(),
	submittedAt: timestamp("submitted_at", { precision: 3, withTimezone: true }),
}, (table) => [
	index("proposal_author_user_id_idx").using("btree", table.authorUserId.asc().nullsLast()),
	index("proposal_polity_id_idx").using("btree", table.polityId.asc().nullsLast()),
	index("proposal_state_tier_idx").using("btree", table.state.asc().nullsLast(), table.tier.asc().nullsLast()),
	unique("proposal_number_key").on(table.number),check("proposal_tier_check", sql`((tier >= 1) AND (tier <= 3))`),]);

export const proposalJob = pgTable("proposal_job", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	proposalId: uuid("proposal_id").notNull().references(() => proposal.id, { onDelete: "cascade" } ),
	revisionN: integer("revision_n"),
	kind: proposalJobKind().notNull(),
	status: proposalJobStatus().default("queued").notNull(),
	requestedBy: uuid("requested_by").references(() => users.id, { onDelete: "set null" } ),
	createdAt: timestamp("created_at", { precision: 3, withTimezone: true }).default(sql`now()`).notNull(),
	startedAt: timestamp("started_at", { precision: 3, withTimezone: true }),
	finishedAt: timestamp("finished_at", { precision: 3, withTimezone: true }),
	output: jsonb(),
}, (table) => [
	index("proposal_job_proposal_id_idx").using("btree", table.proposalId.asc().nullsLast()),
	index("proposal_job_queued_idx").using("btree", table.createdAt.asc().nullsLast()).where(sql`(status = 'queued'::proposal_job_status)`),
]);

export const proposalRevision = pgTable("proposal_revision", {
	proposalId: uuid("proposal_id").notNull().references(() => proposal.id, { onDelete: "cascade" } ),
	n: integer().notNull(),
	changesetId: uuid("changeset_id").references(() => changeset.id, { onDelete: "set null" } ),
	payload: jsonb().notNull(),
	diff: jsonb(),
	checks: jsonb(),
	note: text(),
	createdAt: timestamp("created_at", { precision: 3, withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.proposalId, table.n], name: "proposal_revision_pkey"}),
	uniqueIndex("proposal_revision_changeset_id_key").using("btree", table.changesetId.asc().nullsLast()),
]);

export const release = pgTable("release", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	calver: text().notNull(),
	watermarkChangesetId: uuid("watermark_changeset_id").notNull(),
	createdAt: timestamp("created_at", { withTimezone: true }).default(sql`now()`).notNull(),
	doi: text(),
	manifest: jsonb(),
	changesetId: uuid("changeset_id"),
}, (table) => [
	foreignKey({
		columns: [table.watermarkChangesetId],
		foreignColumns: [changeset.id],
		name: "release_watermark_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "release_changeset_id_fkey"
	}),
	unique("release_calver_key").on(table.calver),]);

export const reportingUnit = pgTable("reporting_unit", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	k: integer().generatedAlwaysAsIdentity(),
	polityId: uuid("polity_id").notNull(),
	schemeCode: text("scheme_code").notNull(),
	code: text().notNull(),
	parentId: uuid("parent_id"),
	countedWithId: uuid("counted_with_id"),
	areaRelation: areaRelation("area_relation").notNull(),
	areaId: uuid("area_id"),
	location: geometry(),
	kind: reportingUnitKind().default("standard").notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.areaId],
		foreignColumns: [area.id],
		name: "reporting_unit_area_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "reporting_unit_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.countedWithId],
		foreignColumns: [table.id],
		name: "reporting_unit_counted_with_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "reporting_unit_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.parentId],
		foreignColumns: [table.id],
		name: "reporting_unit_parent_id_fkey"
	}),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "reporting_unit_polity_id_fkey"
	}),
	foreignKey({
		columns: [table.schemeCode],
		foreignColumns: [scheme.code],
		name: "reporting_unit_scheme_code_fkey"
	}),
	index("reporting_unit_area_idx").using("btree", table.areaId.asc().nullsLast()),
	index("reporting_unit_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("reporting_unit_counted_with_idx").using("btree", table.countedWithId.asc().nullsLast()),
	index("reporting_unit_parent_idx").using("btree", table.parentId.asc().nullsLast()),
	index("reporting_unit_polity_idx").using("btree", table.polityId.asc().nullsLast()),
	unique("reporting_unit_k_key").on(table.k),	unique("reporting_unit_scheme_code_code_valid_key").on(table.schemeCode, table.code, table.valid),check("reporting_unit_check", sql`((area_relation = 'unlocated'::area_relation) = (area_id IS NULL))`),]);

export const reportingUnitArea = pgTable("reporting_unit_area", {
	unitId: uuid("unit_id").notNull(),
	areaId: uuid("area_id").notNull(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	approximate: boolean().default(false).notNull(),
	partial: boolean().default(false).notNull(),
	method: unitAreaMethod(),
}, (table) => [
	primaryKey({ columns: [table.unitId, table.areaId, table.valid], name: "reporting_unit_area_pkey"}),
	foreignKey({
		columns: [table.areaId],
		foreignColumns: [area.id],
		name: "reporting_unit_area_area_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "reporting_unit_area_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.unitId],
		foreignColumns: [reportingUnit.id],
		name: "reporting_unit_area_unit_id_fkey"
	}).onDelete("cascade"),
	index("reporting_unit_area_area_idx").using("btree", table.areaId.asc().nullsLast()),
	index("reporting_unit_area_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const representationGap = pgTable("representation_gap", {
	bodyId: uuid("body_id").notNull(),
	areaId: uuid("area_id").notNull(),
	reason: representationGapReason().notNull(),
	validFrom: date("valid_from").notNull(),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.bodyId, table.areaId, table.valid], name: "representation_gap_pkey"}),
	foreignKey({
		columns: [table.areaId],
		foreignColumns: [area.id],
		name: "representation_gap_area_id_fkey"
	}),
	foreignKey({
		columns: [table.bodyId],
		foreignColumns: [body.id],
		name: "representation_gap_body_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "representation_gap_changeset_id_fkey"
	}),
	index("representation_gap_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
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
	index("result_rollup_sum_area_k_idx").using("btree", table.areaK.asc().nullsLast(), table.resultSetK.asc().nullsLast()),
]);

export const resultRollupUnit = pgTable("result_rollup_unit", {
	resultSetK: integer("result_set_k").notNull(),
	areaK: integer("area_k").notNull(),
	unitK: integer("unit_k").notNull(),
}, (table) => [
	primaryKey({ columns: [table.resultSetK, table.areaK], name: "result_rollup_unit_pkey"}),
	index("result_rollup_unit_area_k_idx").using("btree", table.areaK.asc().nullsLast(), table.resultSetK.asc().nullsLast()),
]);

export const resultSet = pgTable("result_set", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	k: integer().generatedAlwaysAsIdentity(),
	electionId: uuid("election_id").notNull(),
	contestId: uuid("contest_id"),
	stage: stage().notNull(),
	kind: resultKind().default("counted").notNull(),
	sourceId: uuid("source_id").notNull(),
	asOf: timestamp("as_of", { withTimezone: true }),
	status: resultSetStatus().default("active").notNull(),
	isDefault: boolean("is_default").default(false).notNull(),
	supersedesId: uuid("supersedes_id"),
	completeness: numeric(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "result_set_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.contestId],
		foreignColumns: [contest.id],
		name: "result_set_contest_id_fkey"
	}),
	foreignKey({
		columns: [table.electionId],
		foreignColumns: [election.id],
		name: "result_set_election_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "result_set_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "result_set_source_id_fkey"
	}),
	foreignKey({
		columns: [table.supersedesId],
		foreignColumns: [table.id],
		name: "result_set_supersedes_id_fkey"
	}),
	index("result_set_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("result_set_contest_idx").using("btree", table.contestId.asc().nullsLast()),
	uniqueIndex("result_set_default_idx").using("btree", table.electionId.asc().nullsLast(), table.contestId.asc().nullsLast(), table.stage.asc().nullsLast()).where(sql`is_default`),
	index("result_set_supersedes_idx").using("btree", table.supersedesId.asc().nullsLast()),
	unique("result_set_k_key").on(table.k),]);

export const resultSetDrop = pgTable("result_set_drop", {
	resultSetId: uuid("result_set_id").primaryKey(),
	sourceId: uuid("source_id").notNull(),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "result_set_drop_changeset_id_fkey"
	}),
	index("result_set_drop_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("result_set_drop_reason_check", sql`(reason <> ''::text)`),]);

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
	index("result_value_unit_k_idx").using("btree", table.unitK.asc().nullsLast()),
]);

export const resultsAccess = pgTable("results_access", {
	id: uuid().primaryKey(),
	polityId: uuid("polity_id").notNull(),
	serves: text().default("administered").notNull(),
	platform: text().notNull(),
	urlPattern: text("url_pattern").notNull(),
	format: text().notNull(),
	access: resultsPlatformAccess().notNull(),
	firstDate: date("first_date"),
	lastDate: date("last_date"),
	checkedAt: timestamp("checked_at", { withTimezone: true }).notNull(),
	evidence: text(),
	validFrom: date("valid_from"),
	validTo: date("valid_to"),
	sourceId: uuid("source_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "results_access_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "results_access_polity_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "results_access_source_id_fkey"
	}),
	index("results_access_polity_idx").using("btree", table.polityId.asc().nullsLast()),
check("results_access_check", sql`((valid_to IS NULL) OR (valid_from IS NULL) OR (valid_to > valid_from))`),]);

export const rollupCoverage = pgTable("rollup_coverage", {
	resultSetK: integer("result_set_k").notNull(),
	areaK: integer("area_k").notNull(),
	reporting: integer().notNull(),
	expected: integer().notNull(),
}, (table) => [
	primaryKey({ columns: [table.resultSetK, table.areaK], name: "rollup_coverage_pkey"}),
]);

export const rowDrop = pgTable("row_drop", {
	id: uuid().primaryKey(),
	relation: text().notNull(),
	key: jsonb().notNull(),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "row_drop_changeset_id_fkey"
	}),
	index("row_drop_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("row_drop_key_check", sql`((jsonb_typeof(key) = 'object'::text) AND (key <> '{}'::jsonb))`),check("row_drop_reason_check", sql`(reason <> ''::text)`),check("row_drop_relation_check", sql`(relation = ANY (ARRAY['citation'::text, 'label'::text, 'party_color'::text, 'party_image'::text, 'person_image'::text, 'translation'::text, 'alliance_member'::text, 'candidacy_nomination'::text, 'government_party'::text, 'tenure_affiliation'::text, 'area_event_member'::text, 'party_relation'::text, 'party_affiliation'::text, 'source_artifact'::text]))`),]);

export const scheme = pgTable("scheme", {
	code: text().primaryKey(),
	polityId: uuid("polity_id"),
	kind: schemeKind().notNull(),
	rank: smallint().notNull(),
	noun: text().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "scheme_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "scheme_polity_id_fkey"
	}),
	index("scheme_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("scheme_code_check", sql`(code ~ '^([a-z]{2,3}(-[a-z0-9]+)?|world)\\.[a-z0-9_]+$'::text)`),check("scheme_polity_check", sql`((polity_id IS NOT NULL) OR (code ~~ 'world.%'::text))`),]);

export const searchDocument = pgTable("search_document", {
	entityId: uuid("entity_id").primaryKey(),
	type: entityType().notNull(),
	polityId: uuid("polity_id"),
	text: text().notNull(),
	tsv: customType({ dataType: () => 'tsvector' })().notNull(),
	rankBoost: real("rank_boost").default(0).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "search_document_entity_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.polityId],
		foreignColumns: [polity.id],
		name: "search_document_polity_id_fkey"
	}),
	index("search_document_polity_idx").using("btree", table.polityId.asc().nullsLast()),
	index("search_document_trgm_idx").using("gin", table.text.asc().nullsLast().op("gin_trgm_ops")),
	index("search_document_tsv_idx").using("gin", table.tsv.asc().nullsLast()),
]);

export const seat = pgTable("seat", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	bodyId: uuid("body_id").notNull(),
	districtAreaId: uuid("district_area_id"),
	label: text(),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	voting: boolean().default(true).notNull(),
	apportionedAreaId: uuid("apportioned_area_id"),
}, (table) => [
	foreignKey({
		columns: [table.apportionedAreaId],
		foreignColumns: [area.id],
		name: "seat_apportioned_area_id_fkey"
	}),
	foreignKey({
		columns: [table.bodyId],
		foreignColumns: [body.id],
		name: "seat_body_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "seat_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.districtAreaId],
		foreignColumns: [area.id],
		name: "seat_district_area_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "seat_id_fkey"
	}).onDelete("cascade"),
	index("seat_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const slug = pgTable("slug", {
	path: text().primaryKey(),
	entityId: uuid("entity_id").notNull(),
	isCurrent: boolean("is_current").default(true).notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "slug_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.entityId],
		foreignColumns: [entity.id],
		name: "slug_entity_id_fkey"
	}).onDelete("cascade"),
	index("slug_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	uniqueIndex("slug_current_idx").using("btree", table.entityId.asc().nullsLast()).where(sql`is_current`),
	index("slug_entity_idx").using("btree", table.entityId.asc().nullsLast()),
]);

export const source = pgTable("source", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	kind: sourceKind().notNull(),
	publisher: text().notNull(),
	title: text().notNull(),
	url: text(),
	license: text(),
	licenseUrl: text("license_url"),
	attribution: text(),
	notes: text(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "source_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "source_id_fkey"
	}).onDelete("cascade"),
	index("source_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("source_icpsr_idx").using("btree", table.id.asc().nullsLast()).where(sql`(license ~~* '%ICPSR%'::text)`),
]);

export const sourceArtifact = pgTable("source_artifact", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	sourceId: uuid("source_id").notNull(),
	url: text(),
	sha256: text().notNull(),
	retrievedAt: timestamp("retrieved_at", { withTimezone: true }).notNull(),
	storageKey: text("storage_key").notNull(),
	mediaType: text("media_type"),
	bytes: bigint({ mode: 'number' }),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "source_artifact_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "source_artifact_source_id_fkey"
	}),
	index("source_artifact_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const sourcePage = pgTable("source_page", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	artifactId: uuid("artifact_id").notNull(),
	pageNumber: integer("page_number").notNull(),
	imageKey: text("image_key").notNull(),
	width: integer().notNull(),
	height: integer().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.artifactId],
		foreignColumns: [sourceArtifact.id],
		name: "source_page_artifact_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "source_page_changeset_id_fkey"
	}),
	index("source_page_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	unique("source_page_artifact_id_page_number_key").on(table.artifactId, table.pageNumber),]);

export const statewideOfficeYear = pgTable("statewide_office_year", {
	state: text().notNull(),
	officeId: uuid("office_id").notNull(),
	class: text().notNull(),
	year: integer().notNull(),
	jointTicket: boolean("joint_ticket").notNull(),
	basis: text().notNull(),
}, (table) => [
	primaryKey({ columns: [table.officeId, table.year], name: "statewide_office_year_pkey"}),
	foreignKey({
		columns: [table.officeId],
		foreignColumns: [office.id],
		name: "statewide_office_year_office_id_fkey"
	}),
	index("statewide_office_year_state_idx").using("btree", table.state.asc().nullsLast(), table.year.asc().nullsLast()),
]);

export const tenure = pgTable("tenure", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	personId: uuid("person_id").notNull(),
	officeId: uuid("office_id"),
	seatId: uuid("seat_id"),
	bodyId: uuid("body_id"),
	fromDate: date("from_date"),
	fromPrecision: datePrecision("from_precision").default("day").notNull(),
	toDate: date("to_date"),
	toPrecision: datePrecision("to_precision").default("day").notNull(),
	acquiredVia: acquiredVia("acquired_via").notNull(),
	contestId: uuid("contest_id"),
	endedVia: endedVia("ended_via"),
	resumesTenureId: uuid("resumes_tenure_id"),
	succeedsTenureId: uuid("succeeds_tenure_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.bodyId],
		foreignColumns: [body.id],
		name: "tenure_body_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "tenure_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.contestId],
		foreignColumns: [contest.id],
		name: "tenure_contest_id_fkey"
	}),
	foreignKey({
		columns: [table.id],
		foreignColumns: [entity.id],
		name: "tenure_id_fkey"
	}).onDelete("cascade"),
	foreignKey({
		columns: [table.officeId],
		foreignColumns: [office.id],
		name: "tenure_office_id_fkey"
	}),
	foreignKey({
		columns: [table.personId],
		foreignColumns: [person.id],
		name: "tenure_person_id_fkey"
	}),
	foreignKey({
		columns: [table.resumesTenureId],
		foreignColumns: [table.id],
		name: "tenure_resumes_tenure_id_fkey"
	}),
	foreignKey({
		columns: [table.seatId],
		foreignColumns: [seat.id],
		name: "tenure_seat_id_fkey"
	}),
	foreignKey({
		columns: [table.succeedsTenureId],
		foreignColumns: [table.id],
		name: "tenure_succeeds_tenure_id_fkey"
	}),
	index("tenure_body_idx").using("btree", table.bodyId.asc().nullsLast()),
	index("tenure_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	index("tenure_contest_idx").using("btree", table.contestId.asc().nullsLast()),
	index("tenure_office_idx").using("btree", table.officeId.asc().nullsLast()),
	index("tenure_person_idx").using("btree", table.personId.asc().nullsLast()),
check("tenure_check", sql`(num_nonnulls(office_id, body_id) >= 1)`),]);

export const tenureAffiliation = pgTable("tenure_affiliation", {
	tenureId: uuid("tenure_id").notNull(),
	kind: tenureAffiliationKind().default("party").notNull(),
	partyId: uuid("party_id"),
	validFrom: date("valid_from"),
	validFromPrecision: datePrecision("valid_from_precision").default("day").notNull(),
	validTo: date("valid_to"),
	validToPrecision: datePrecision("valid_to_precision").default("day").notNull(),
	valid: daterange().notNull().generatedAlwaysAs(sql`daterange(valid_from, valid_to, '[)'::text)`),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "tenure_affiliation_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.partyId],
		foreignColumns: [party.id],
		name: "tenure_affiliation_party_id_fkey"
	}),
	foreignKey({
		columns: [table.tenureId],
		foreignColumns: [tenure.id],
		name: "tenure_affiliation_tenure_id_fkey"
	}).onDelete("cascade"),
	index("tenure_affiliation_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
	unique("tenure_affiliation_key").on(table.tenureId, table.kind, table.partyId, table.valid),]);

export const tenureEvent = pgTable("tenure_event", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	tenureId: uuid("tenure_id").notNull(),
	kind: tenureEventKind().notNull(),
	date: date(),
	datePrecision: datePrecision("date_precision").default("day").notNull(),
	actor: text(),
	reason: text(),
	sourceId: uuid("source_id"),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "tenure_event_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "tenure_event_source_id_fkey"
	}),
	foreignKey({
		columns: [table.tenureId],
		foreignColumns: [tenure.id],
		name: "tenure_event_tenure_id_fkey"
	}).onDelete("cascade"),
	index("tenure_event_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
]);

export const thread = pgTable("thread", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	kind: threadKind().notNull(),
	proposalId: uuid("proposal_id"),
	entityId: uuid("entity_id"),
	title: text(),
	status: threadStatus().default("open").notNull(),
	authorUserId: uuid("author_user_id"),
	createdAt: timestamp("created_at", { precision: 3, withTimezone: true }).default(sql`now()`).notNull(),
	updatedAt: timestamp("updated_at", { precision: 3, withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.authorUserId],
		foreignColumns: [users.id],
		name: "thread_author_user_id_fkey"
	}).onDelete("set null"),
	foreignKey({
		columns: [table.proposalId],
		foreignColumns: [proposal.id],
		name: "thread_proposal_id_fkey"
	}).onDelete("cascade"),
	index("thread_entity_id_idx").using("btree", table.entityId.asc().nullsLast(), table.updatedAt.desc().nullsFirst()).where(sql`(kind = 'talk'::thread_kind)`),
	uniqueIndex("thread_proposal_id_key").using("btree", table.proposalId.asc().nullsLast()),
check("thread_kind_check", sql`
CASE kind
    WHEN 'proposal'::thread_kind THEN ((proposal_id IS NOT NULL) AND (entity_id IS NULL) AND (title IS NULL))
    ELSE ((proposal_id IS NULL) AND (entity_id IS NOT NULL) AND ((char_length(title) >= 1) AND (char_length(title) <= 200)))
END`),]);

export const translation = pgTable("translation", {
	sourceMd5: text("source_md5").notNull(),
	lang: text().notNull(),
	body: text().notNull(),
	origin: translationOrigin().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
	sourceLang: text("source_lang").default("en").notNull(),
	sourceId: uuid("source_id"),
	staleFrom: text("stale_from"),
}, (table) => [
	primaryKey({ columns: [table.sourceMd5, table.lang], name: "translation_pkey"}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "translation_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "translation_source_id_fkey"
	}),
	index("translation_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("translation_body_nfc", sql`(body IS NFC NORMALIZED)`),check("translation_lang_bcp47", sql`(lang ~ '^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$'::text)`),check("translation_lang_check", sql`(lang <> source_lang)`),check("translation_official_source", sql`((origin <> 'official'::translation_origin) OR (source_id IS NOT NULL))`),check("translation_source_lang_bcp47", sql`(source_lang ~ '^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$'::text)`),check("translation_source_md5_check", sql`(source_md5 ~ '^[0-9a-f]{32}$'::text)`),check("translation_stale_from_check", sql`((stale_from ~ '^[0-9a-f]{32}$'::text) AND (stale_from <> source_md5))`),]);

export const unitAreaDrop = pgTable("unit_area_drop", {
	unitId: uuid("unit_id").notNull(),
	areaId: uuid("area_id").notNull(),
	reason: text().notNull(),
	changesetId: uuid("changeset_id").notNull(),
	recordedFrom: timestamp("recorded_from", { withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.unitId, table.areaId], name: "unit_area_drop_pkey"}),
	foreignKey({
		columns: [table.areaId],
		foreignColumns: [area.id],
		name: "unit_area_drop_area_id_fkey"
	}),
	foreignKey({
		columns: [table.changesetId],
		foreignColumns: [changeset.id],
		name: "unit_area_drop_changeset_id_fkey"
	}),
	foreignKey({
		columns: [table.unitId],
		foreignColumns: [reportingUnit.id],
		name: "unit_area_drop_unit_id_fkey"
	}).onDelete("cascade"),
	index("unit_area_drop_changeset_id_idx").using("btree", table.changesetId.asc().nullsLast()),
check("unit_area_drop_reason_check", sql`(reason <> ''::text)`),]);

export const upload = pgTable("upload", {
	id: uuid().default(sql`uuidv7()`).primaryKey(),
	ownerUserId: uuid("owner_user_id"),
	agentKeyId: uuid("agent_key_id"),
	sha256: text().notNull(),
	mediaType: text("media_type").notNull(),
	bytes: bigint({ mode: 'number' }).notNull(),
	filename: text().notNull(),
	storageKey: text("storage_key"),
	pages: integer(),
	status: uploadStatus().default("received").notNull(),
	error: text(),
	publicRecord: boolean("public_record").default(false).notNull(),
	createdAt: timestamp("created_at", { precision: 3, withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	foreignKey({
		columns: [table.agentKeyId],
		foreignColumns: [apikeys.id],
		name: "upload_agent_key_id_fkey"
	}).onDelete("set null"),
	foreignKey({
		columns: [table.ownerUserId],
		foreignColumns: [users.id],
		name: "upload_owner_user_id_fkey"
	}).onDelete("set null"),
	uniqueIndex("upload_owner_sha256_idx").using("btree", table.ownerUserId.asc().nullsLast(), table.sha256.asc().nullsLast()),
	index("upload_sha256_idx").using("btree", table.sha256.asc().nullsLast()),
check("upload_bytes_check", sql`((bytes > 0) AND (bytes <= 209715200))`),check("upload_ready_check", sql`((status <> 'ready'::upload_status) OR (storage_key IS NOT NULL))`),check("upload_sha256_check", sql`(sha256 ~ '^[0-9a-f]{64}$'::text)`),]);

export const uploadPage = pgTable("upload_page", {
	uploadId: uuid("upload_id").notNull(),
	pageNumber: integer("page_number").notNull(),
	imageKey: text("image_key").notNull(),
	width: integer().notNull(),
	height: integer().notNull(),
	textLayer: boolean("text_layer").default(false).notNull(),
}, (table) => [
	primaryKey({ columns: [table.uploadId, table.pageNumber], name: "upload_page_pkey"}),
	foreignKey({
		columns: [table.uploadId],
		foreignColumns: [upload.id],
		name: "upload_page_upload_id_fkey"
	}).onDelete("cascade"),
check("upload_page_number_check", sql`(page_number > 0)`),]);

export const valueLocator = pgTable("value_locator", {
	resultSetK: integer("result_set_k").notNull(),
	unitK: integer("unit_k").notNull(),
	optionK: integer("option_k").default(0).notNull(),
	channel: smallint().default(0).notNull(),
	measure: smallint().notNull(),
	countRound: smallint("count_round").default(0).notNull(),
	pageId: uuid("page_id"),
	sourceId: uuid("source_id"),
	x0: real(),
	y0: real(),
	x1: real(),
	y1: real(),
}, (table) => [
	primaryKey({ columns: [table.resultSetK, table.unitK, table.optionK, table.channel, table.measure, table.countRound], name: "value_locator_pkey"}),
	foreignKey({
		columns: [table.pageId],
		foreignColumns: [sourcePage.id],
		name: "value_locator_page_id_fkey"
	}),
	foreignKey({
		columns: [table.sourceId],
		foreignColumns: [source.id],
		name: "value_locator_source_id_fkey"
	}),
check("value_locator_check", sql`(num_nonnulls(page_id, source_id) >= 1)`),check("value_locator_check1", sql`((page_id IS NULL) = (x0 IS NULL))`),]);

export const watch = pgTable("watch", {
	userId: uuid("user_id").notNull(),
	targetKind: watchTarget("target_kind").notNull(),
	targetId: uuid("target_id").notNull(),
	descendants: boolean().default(false).notNull(),
	createdAt: timestamp("created_at", { precision: 3, withTimezone: true }).default(sql`now()`).notNull(),
}, (table) => [
	primaryKey({ columns: [table.userId, table.targetKind, table.targetId], name: "watch_pkey"}),
	foreignKey({
		columns: [table.userId],
		foreignColumns: [users.id],
		name: "watch_user_id_fkey"
	}).onDelete("cascade"),
	index("watch_target_idx").using("btree", table.targetKind.asc().nullsLast(), table.targetId.asc().nullsLast()),
]);
export const resultRollup = pgView("result_rollup", {	resultSetK: integer("result_set_k"),
	areaK: integer("area_k"),
	optionK: integer("option_k"),
	channel: smallint(),
	measure: smallint(),
	countRound: smallint("count_round"),
	complete: boolean(),
	value: numeric(),
}).as(sql`SELECT result_rollup_sum.result_set_k, result_rollup_sum.area_k, result_rollup_sum.option_k, result_rollup_sum.channel, result_rollup_sum.measure, result_rollup_sum.count_round, result_rollup_sum.complete, result_rollup_sum.value FROM result_rollup_sum UNION ALL SELECT u.result_set_k, u.area_k, v.option_k, v.channel, v.measure, v.count_round, true AS complete, v.value FROM result_rollup_unit u JOIN result_value v ON v.result_set_k = u.result_set_k AND v.unit_k = u.unit_k`);