import { defineRelations } from "drizzle-orm";
import * as schema from "./core.ts";

export const relations = defineRelations(schema, (r) => ({
	alliance: {
		changeset: r.one.changeset({
			from: r.alliance.changesetId,
			to: r.changeset.id
		}),
		election: r.one.election({
			from: r.alliance.electionId,
			to: r.election.id
		}),
		entity: r.one.entity({
			from: r.alliance.id,
			to: r.entity.id
		}),
		alliance: r.one.alliance({
			from: r.alliance.parentAllianceId,
			to: r.alliance.id,
			alias: "alliance_parentAllianceId_alliance_id"
		}),
		alliances: r.many.alliance({
			alias: "alliance_parentAllianceId_alliance_id"
		}),
		allianceMembers: r.many.allianceMember(),
		contestOptions: r.many.contestOption(),
	},
	changeset: {
		alliances: r.many.alliance(),
		allianceMembers: r.many.allianceMember(),
		areasChangesetId: r.many.area({
			alias: "area_changesetId_changeset_id"
		}),
		areasViaAreaCode: r.many.area({
			alias: "area_id_changeset_id_via_areaCode"
		}),
		sourcesViaAreaEvent: r.many.source({
			from: r.changeset.id.through(r.areaEvent.changesetId),
			to: r.source.id.through(r.areaEvent.sourceId),
			alias: "changeset_id_source_id_via_areaEvent"
		}),
		areaEventMembers: r.many.areaEventMember(),
		areaGeometries: r.many.areaGeometry(),
		areaParents: r.many.areaParent(),
		contestsViaBallotQuestion: r.many.contest({
			from: r.changeset.id.through(r.ballotQuestion.changesetId),
			to: r.contest.id.through(r.ballotQuestion.contestId),
			alias: "changeset_id_contest_id_via_ballotQuestion"
		}),
		ballotQuestionTexts: r.many.ballotQuestionText(),
		bodiesChangesetId: r.many.body({
			alias: "body_changesetId_changeset_id"
		}),
		bodyApportionments: r.many.bodyApportionment(),
		bodyEvents: r.many.bodyEvent(),
		bodiesViaBodySize: r.many.body({
			alias: "body_id_changeset_id_via_bodySize"
		}),
		schemes: r.many.scheme({
			from: r.changeset.id.through(r.boundarySet.changesetId),
			to: r.scheme.code.through(r.boundarySet.schemeCode)
		}),
		candidacies: r.many.candidacy(),
		candidacyNominations: r.many.candidacyNomination(),
		citations: r.many.citation(),
		contestsChangesetId: r.many.contest({
			alias: "contest_changesetId_changeset_id"
		}),
		contestOptions: r.many.contestOption(),
		crosswalks: r.many.crosswalk(),
		elections: r.many.election(),
		electoralSystems: r.many.electoralSystem(),
		entityMerges: r.many.entityMerge(),
		entitiesViaEntityRetirement: r.many.entity({
			from: r.changeset.id.through(r.entityRetirement.changesetId),
			to: r.entity.id.through(r.entityRetirement.entityId),
			alias: "changeset_id_entity_id_via_entityRetirement"
		}),
		entitiesViaEntitySubtypeDrop: r.many.entity({
			from: r.changeset.id.through(r.entitySubtypeDrop.changesetId),
			to: r.entity.id.through(r.entitySubtypeDrop.entityId),
			alias: "changeset_id_entity_id_via_entitySubtypeDrop"
		}),
		entitiesViaExternalId: r.many.entity({
			from: r.changeset.id.through(r.externalId.changesetId),
			to: r.entity.id.through(r.externalId.entityId),
			alias: "changeset_id_entity_id_via_externalId"
		}),
		factRekeys: r.many.factRekey(),
		financeSummaries: r.many.financeSummary(),
		flags: r.many.flag(),
		governments: r.many.government(),
		governmentParties: r.many.governmentParty(),
		sourcesViaIndicator: r.many.source({
			from: r.changeset.id.through(r.indicator.changesetId),
			to: r.source.id.through(r.indicator.sourceId),
			alias: "changeset_id_source_id_via_indicator"
		}),
		labels: r.many.label(),
		membershipDrops: r.many.membershipDrop(),
		names: r.many.name(),
		entitiesViaNameDrop: r.many.entity({
			from: r.changeset.id.through(r.nameDrop.changesetId),
			to: r.entity.id.through(r.nameDrop.entityId),
			alias: "changeset_id_entity_id_via_nameDrop"
		}),
		entitiesViaNote: r.many.entity({
			from: r.changeset.id.through(r.note.changesetId),
			to: r.entity.id.through(r.note.entityId),
			alias: "changeset_id_entity_id_via_note"
		}),
		offices: r.many.office(),
		outcomes: r.many.outcome(),
		parties: r.many.party(),
		partyAffiliations: r.many.partyAffiliation(),
		partyColors: r.many.partyColor(),
		partyPositions: r.many.partyPosition(),
		partyRelations: r.many.partyRelation(),
		entitiesViaPerson: r.many.entity({
			from: r.changeset.id.through(r.person.changesetId),
			to: r.entity.id.through(r.person.id),
			alias: "changeset_id_entity_id_via_person"
		}),
		personImages: r.many.personImage(),
		personPartyMemberships: r.many.personPartyMembership(),
		entitiesViaPolity: r.many.entity({
			from: r.changeset.id.through(r.polity.changesetId),
			to: r.entity.id.through(r.polity.id),
			alias: "changeset_id_entity_id_via_polity"
		}),
		polityParents: r.many.polityParent(),
		politiesViaPolityPeriod: r.many.polity({
			from: r.changeset.id.through(r.polityPeriod.changesetId),
			to: r.polity.id.through(r.polityPeriod.polityId),
			alias: "changeset_id_polity_id_via_polityPeriod"
		}),
		polityRelations: r.many.polityRelation(),
		polityTerritories: r.many.polityTerritory(),
		releases: r.many.release(),
		reportingUnits: r.many.reportingUnit(),
		reportingUnitAreas: r.many.reportingUnitArea(),
		representationGaps: r.many.representationGap(),
		resultSets: r.many.resultSet(),
		politiesViaScheme: r.many.polity({
			from: r.changeset.id.through(r.scheme.changesetId),
			to: r.polity.id.through(r.scheme.polityId),
			alias: "changeset_id_polity_id_via_scheme"
		}),
		seats: r.many.seat(),
		entitiesViaSlug: r.many.entity({
			from: r.changeset.id.through(r.slug.changesetId),
			to: r.entity.id.through(r.slug.entityId),
			alias: "changeset_id_entity_id_via_slug"
		}),
		entitiesViaSource: r.many.entity({
			from: r.changeset.id.through(r.source.changesetId),
			to: r.entity.id.through(r.source.id),
			alias: "changeset_id_entity_id_via_source"
		}),
		sourcesViaSourceArtifact: r.many.source({
			from: r.changeset.id.through(r.sourceArtifact.changesetId),
			to: r.source.id.through(r.sourceArtifact.sourceId),
			alias: "changeset_id_source_id_via_sourceArtifact"
		}),
		sourceArtifacts: r.many.sourceArtifact(),
		tenures: r.many.tenure(),
		tenureAffiliations: r.many.tenureAffiliation(),
		tenureEvents: r.many.tenureEvent(),
	},
	election: {
		alliances: r.many.alliance(),
		contests: r.many.contest(),
		changeset: r.one.changeset({
			from: r.election.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.election.id,
			to: r.entity.id
		}),
		electionParentId: r.one.election({
			from: r.election.parentId,
			to: r.election.id,
			alias: "election_parentId_election_id"
		}),
		electionsParentId: r.many.election({
			alias: "election_parentId_election_id"
		}),
		polity: r.one.polity({
			from: r.election.polityId,
			to: r.polity.id
		}),
		electionRerunOfId: r.one.election({
			from: r.election.rerunOfId,
			to: r.election.id,
			alias: "election_rerunOfId_election_id"
		}),
		electionsRerunOfId: r.many.election({
			alias: "election_rerunOfId_election_id"
		}),
		financeSummaries: r.many.financeSummary(),
		governments: r.many.government(),
		partyRelations: r.many.partyRelation(),
		resultSets: r.many.resultSet(),
	},
	entity: {
		alliances: r.many.alliance(),
		areas: r.many.area(),
		bodies: r.many.body(),
		candidacies: r.many.candidacy(),
		citations: r.many.citation(),
		contests: r.many.contest(),
		contestOptions: r.many.contestOption(),
		elections: r.many.election(),
		electoralSystems: r.many.electoralSystem(),
		entityMergesEntityId: r.many.entityMerge({
			alias: "entityMerge_entityId_entity_id"
		}),
		entityMergesIntoId: r.many.entityMerge({
			alias: "entityMerge_intoId_entity_id"
		}),
		changesetsViaEntityRetirement: r.many.changeset({
			alias: "changeset_id_entity_id_via_entityRetirement"
		}),
		changesetsViaEntitySubtypeDrop: r.many.changeset({
			alias: "changeset_id_entity_id_via_entitySubtypeDrop"
		}),
		changesetsViaExternalId: r.many.changeset({
			alias: "changeset_id_entity_id_via_externalId"
		}),
		financeSummaries: r.many.financeSummary(),
		flags: r.many.flag(),
		governments: r.many.government(),
		labels: r.many.label(),
		names: r.many.name(),
		changesetsViaNameDrop: r.many.changeset({
			alias: "changeset_id_entity_id_via_nameDrop"
		}),
		changesetsViaNote: r.many.changeset({
			alias: "changeset_id_entity_id_via_note"
		}),
		offices: r.many.office(),
		parties: r.many.party(),
		changesetsViaPerson: r.many.changeset({
			alias: "changeset_id_entity_id_via_person"
		}),
		changesetsViaPolity: r.many.changeset({
			alias: "changeset_id_entity_id_via_polity"
		}),
		reportingUnits: r.many.reportingUnit(),
		resultSets: r.many.resultSet(),
		polities: r.many.polity({
			from: r.entity.id.through(r.searchDocument.entityId),
			to: r.polity.id.through(r.searchDocument.polityId)
		}),
		seats: r.many.seat(),
		changesetsViaSlug: r.many.changeset({
			alias: "changeset_id_entity_id_via_slug"
		}),
		changesetsViaSource: r.many.changeset({
			alias: "changeset_id_entity_id_via_source"
		}),
		tenures: r.many.tenure(),
	},
	allianceMember: {
		alliance: r.one.alliance({
			from: r.allianceMember.allianceId,
			to: r.alliance.id
		}),
		changeset: r.one.changeset({
			from: r.allianceMember.changesetId,
			to: r.changeset.id
		}),
		party: r.one.party({
			from: r.allianceMember.partyId,
			to: r.party.id
		}),
	},
	party: {
		allianceMembers: r.many.allianceMember(),
		candidacies: r.many.candidacy(),
		candidacyNominations: r.many.candidacyNomination(),
		contests: r.many.contest(),
		contestOptions: r.many.contestOption(),
		governmentParties: r.many.governmentParty(),
		membershipDrops: r.many.membershipDrop(),
		changeset: r.one.changeset({
			from: r.party.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.party.id,
			to: r.entity.id
		}),
		polity: r.one.polity({
			from: r.party.polityId,
			to: r.polity.id
		}),
		partyAffiliationsParentPartyId: r.many.partyAffiliation({
			alias: "partyAffiliation_parentPartyId_party_id"
		}),
		partyAffiliationsPartyId: r.many.partyAffiliation({
			alias: "partyAffiliation_partyId_party_id"
		}),
		partyColors: r.many.partyColor(),
		partyPositions: r.many.partyPosition(),
		partyRelationsFromPartyId: r.many.partyRelation({
			alias: "partyRelation_fromPartyId_party_id"
		}),
		partyRelationsToPartyId: r.many.partyRelation({
			alias: "partyRelation_toPartyId_party_id"
		}),
		personPartyMemberships: r.many.personPartyMembership(),
		tenureAffiliations: r.many.tenureAffiliation(),
	},
	area: {
		changeset: r.one.changeset({
			from: r.area.changesetId,
			to: r.changeset.id,
			alias: "area_changesetId_changeset_id"
		}),
		entity: r.one.entity({
			from: r.area.id,
			to: r.entity.id
		}),
		scheme: r.one.scheme({
			from: r.area.schemeCode,
			to: r.scheme.code
		}),
		changesets: r.many.changeset({
			from: r.area.id.through(r.areaCode.areaId),
			to: r.changeset.id.through(r.areaCode.changesetId),
			alias: "area_id_changeset_id_via_areaCode"
		}),
		areaEventMembers: r.many.areaEventMember(),
		areaGeometries: r.many.areaGeometry(),
		areaParentsAreaId: r.many.areaParent({
			alias: "areaParent_areaId_area_id"
		}),
		areaParentsParentId: r.many.areaParent({
			alias: "areaParent_parentId_area_id"
		}),
		bodyApportionments: r.many.bodyApportionment(),
		contests: r.many.contest(),
		polityTerritories: r.many.polityTerritory(),
		reportingUnits: r.many.reportingUnit(),
		reportingUnitAreas: r.many.reportingUnitArea(),
		representationGaps: r.many.representationGap(),
		seatsApportionedAreaId: r.many.seat({
			alias: "seat_apportionedAreaId_area_id"
		}),
		seatsDistrictAreaId: r.many.seat({
			alias: "seat_districtAreaId_area_id"
		}),
	},
	scheme: {
		areas: r.many.area(),
		changesets: r.many.changeset(),
		crosswalks: r.many.crosswalk(),
		reportingUnits: r.many.reportingUnit(),
	},
	source: {
		changesetsViaAreaEvent: r.many.changeset({
			alias: "changeset_id_source_id_via_areaEvent"
		}),
		areaGeometries: r.many.areaGeometry(),
		ballotQuestionTexts: r.many.ballotQuestionText(),
		bodyEvents: r.many.bodyEvent(),
		citations: r.many.citation(),
		financeSummaries: r.many.financeSummary(),
		governments: r.many.government(),
		changesetsViaIndicator: r.many.changeset({
			alias: "changeset_id_source_id_via_indicator"
		}),
		labels: r.many.label(),
		names: r.many.name(),
		partyColors: r.many.partyColor(),
		partyPositions: r.many.partyPosition(),
		partyRelations: r.many.partyRelation(),
		personImages: r.many.personImage(),
		personPartyMemberships: r.many.personPartyMembership(),
		polityRelations: r.many.polityRelation(),
		resultSets: r.many.resultSet(),
		changesetsViaSourceArtifact: r.many.changeset({
			alias: "changeset_id_source_id_via_sourceArtifact"
		}),
		tenureEvents: r.many.tenureEvent(),
		sourcePages: r.many.sourcePage(),
	},
	areaEventMember: {
		area: r.one.area({
			from: r.areaEventMember.areaId,
			to: r.area.id
		}),
		changeset: r.one.changeset({
			from: r.areaEventMember.changesetId,
			to: r.changeset.id
		}),
		areaEvent: r.one.areaEvent({
			from: r.areaEventMember.eventId,
			to: r.areaEvent.id
		}),
	},
	areaEvent: {
		areaEventMembers: r.many.areaEventMember(),
	},
	areaGeometry: {
		area: r.one.area({
			from: r.areaGeometry.areaId,
			to: r.area.id
		}),
		changeset: r.one.changeset({
			from: r.areaGeometry.changesetId,
			to: r.changeset.id
		}),
		source: r.one.source({
			from: r.areaGeometry.sourceId,
			to: r.source.id
		}),
	},
	areaParent: {
		areaAreaId: r.one.area({
			from: r.areaParent.areaId,
			to: r.area.id,
			alias: "areaParent_areaId_area_id"
		}),
		changeset: r.one.changeset({
			from: r.areaParent.changesetId,
			to: r.changeset.id
		}),
		areaParentId: r.one.area({
			from: r.areaParent.parentId,
			to: r.area.id,
			alias: "areaParent_parentId_area_id"
		}),
	},
	contest: {
		changesets: r.many.changeset({
			alias: "changeset_id_contest_id_via_ballotQuestion"
		}),
		contestAllocationParentId: r.one.contest({
			from: r.contest.allocationParentId,
			to: r.contest.id,
			alias: "contest_allocationParentId_contest_id"
		}),
		contestsAllocationParentId: r.many.contest({
			alias: "contest_allocationParentId_contest_id"
		}),
		body: r.one.body({
			from: r.contest.bodyId,
			to: r.body.id
		}),
		boundarySet: r.one.boundarySet({
			from: r.contest.boundarySetId,
			to: r.boundarySet.id
		}),
		changeset: r.one.changeset({
			from: r.contest.changesetId,
			to: r.changeset.id,
			alias: "contest_changesetId_changeset_id"
		}),
		area: r.one.area({
			from: r.contest.districtAreaId,
			to: r.area.id
		}),
		election: r.one.election({
			from: r.contest.electionId,
			to: r.election.id
		}),
		electoralSystem: r.one.electoralSystem({
			from: r.contest.electoralSystemId,
			to: r.electoralSystem.id
		}),
		entity: r.one.entity({
			from: r.contest.id,
			to: r.entity.id
		}),
		office: r.one.office({
			from: r.contest.officeId,
			to: r.office.id
		}),
		party: r.one.party({
			from: r.contest.partyScopeId,
			to: r.party.id
		}),
		contestPreviousRoundId: r.one.contest({
			from: r.contest.previousRoundId,
			to: r.contest.id,
			alias: "contest_previousRoundId_contest_id"
		}),
		contestsPreviousRoundId: r.many.contest({
			alias: "contest_previousRoundId_contest_id"
		}),
		seat: r.one.seat({
			from: r.contest.seatId,
			to: r.seat.id
		}),
		contestOptions: r.many.contestOption(),
		resultSets: r.many.resultSet(),
		tenures: r.many.tenure(),
	},
	ballotQuestionText: {
		changeset: r.one.changeset({
			from: r.ballotQuestionText.changesetId,
			to: r.changeset.id
		}),
		ballotQuestion: r.one.ballotQuestion({
			from: r.ballotQuestionText.contestId,
			to: r.ballotQuestion.contestId
		}),
		source: r.one.source({
			from: r.ballotQuestionText.sourceId,
			to: r.source.id
		}),
	},
	ballotQuestion: {
		ballotQuestionTexts: r.many.ballotQuestionText(),
	},
	body: {
		changeset: r.one.changeset({
			from: r.body.changesetId,
			to: r.changeset.id,
			alias: "body_changesetId_changeset_id"
		}),
		entity: r.one.entity({
			from: r.body.id,
			to: r.entity.id
		}),
		polity: r.one.polity({
			from: r.body.polityId,
			to: r.polity.id
		}),
		bodyApportionments: r.many.bodyApportionment(),
		bodyEvents: r.many.bodyEvent(),
		changesets: r.many.changeset({
			from: r.body.id.through(r.bodySize.bodyId),
			to: r.changeset.id.through(r.bodySize.changesetId),
			alias: "body_id_changeset_id_via_bodySize"
		}),
		contests: r.many.contest(),
		offices: r.many.office(),
		representationGaps: r.many.representationGap(),
		seats: r.many.seat(),
		tenures: r.many.tenure(),
	},
	polity: {
		bodies: r.many.body(),
		elections: r.many.election(),
		electoralSystems: r.many.electoralSystem(),
		governments: r.many.government(),
		offices: r.many.office(),
		parties: r.many.party(),
		polityParentsParentId: r.many.polityParent({
			alias: "polityParent_parentId_polity_id"
		}),
		polityParentsPolityId: r.many.polityParent({
			alias: "polityParent_polityId_polity_id"
		}),
		changesetsViaPolityPeriod: r.many.changeset({
			alias: "changeset_id_polity_id_via_polityPeriod"
		}),
		polityRelationsFromPolityId: r.many.polityRelation({
			alias: "polityRelation_fromPolityId_polity_id"
		}),
		polityRelationsToPolityId: r.many.polityRelation({
			alias: "polityRelation_toPolityId_polity_id"
		}),
		polityTerritories: r.many.polityTerritory(),
		reportingUnits: r.many.reportingUnit(),
		changesetsViaScheme: r.many.changeset({
			alias: "changeset_id_polity_id_via_scheme"
		}),
		entities: r.many.entity(),
	},
	bodyApportionment: {
		area: r.one.area({
			from: r.bodyApportionment.areaId,
			to: r.area.id
		}),
		body: r.one.body({
			from: r.bodyApportionment.bodyId,
			to: r.body.id
		}),
		changeset: r.one.changeset({
			from: r.bodyApportionment.changesetId,
			to: r.changeset.id
		}),
	},
	bodyEvent: {
		body: r.one.body({
			from: r.bodyEvent.bodyId,
			to: r.body.id
		}),
		changeset: r.one.changeset({
			from: r.bodyEvent.changesetId,
			to: r.changeset.id
		}),
		source: r.one.source({
			from: r.bodyEvent.sourceId,
			to: r.source.id
		}),
	},
	candidacy: {
		changeset: r.one.changeset({
			from: r.candidacy.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.candidacy.id,
			to: r.entity.id
		}),
		party: r.one.party({
			from: r.candidacy.memberOfPartyId,
			to: r.party.id
		}),
		contestOption: r.one.contestOption({
			from: r.candidacy.optionId,
			to: r.contestOption.id
		}),
		person: r.one.person({
			from: r.candidacy.personId,
			to: r.person.id
		}),
		candidacyNominations: r.many.candidacyNomination(),
		outcomes: r.many.outcome(),
	},
	contestOption: {
		candidacies: r.many.candidacy(),
		alliance: r.one.alliance({
			from: r.contestOption.allianceId,
			to: r.alliance.id
		}),
		changeset: r.one.changeset({
			from: r.contestOption.changesetId,
			to: r.changeset.id
		}),
		contest: r.one.contest({
			from: r.contestOption.contestId,
			to: r.contest.id
		}),
		entity: r.one.entity({
			from: r.contestOption.id,
			to: r.entity.id
		}),
		contestOption: r.one.contestOption({
			from: r.contestOption.parentOptionId,
			to: r.contestOption.id,
			alias: "contestOption_parentOptionId_contestOption_id"
		}),
		contestOptions: r.many.contestOption({
			alias: "contestOption_parentOptionId_contestOption_id"
		}),
		party: r.one.party({
			from: r.contestOption.partyId,
			to: r.party.id
		}),
		outcomes: r.many.outcome(),
	},
	person: {
		candidacies: r.many.candidacy(),
		governments: r.many.government(),
		membershipDrops: r.many.membershipDrop(),
		personImages: r.many.personImage(),
		personPartyMemberships: r.many.personPartyMembership(),
		tenures: r.many.tenure(),
	},
	candidacyNomination: {
		candidacy: r.one.candidacy({
			from: r.candidacyNomination.candidacyId,
			to: r.candidacy.id
		}),
		changeset: r.one.changeset({
			from: r.candidacyNomination.changesetId,
			to: r.changeset.id
		}),
		party: r.one.party({
			from: r.candidacyNomination.partyId,
			to: r.party.id
		}),
	},
	citation: {
		changeset: r.one.changeset({
			from: r.citation.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.citation.entityId,
			to: r.entity.id
		}),
		source: r.one.source({
			from: r.citation.sourceId,
			to: r.source.id
		}),
	},
	boundarySet: {
		contests: r.many.contest(),
		crosswalksFromBoundarySetId: r.many.crosswalk({
			alias: "crosswalk_fromBoundarySetId_boundarySet_id"
		}),
		crosswalksToBoundarySetId: r.many.crosswalk({
			alias: "crosswalk_toBoundarySetId_boundarySet_id"
		}),
	},
	electoralSystem: {
		contests: r.many.contest(),
		changeset: r.one.changeset({
			from: r.electoralSystem.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.electoralSystem.id,
			to: r.entity.id
		}),
		polity: r.one.polity({
			from: r.electoralSystem.polityId,
			to: r.polity.id
		}),
	},
	office: {
		contests: r.many.contest(),
		governments: r.many.government(),
		body: r.one.body({
			from: r.office.bodyId,
			to: r.body.id
		}),
		changeset: r.one.changeset({
			from: r.office.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.office.id,
			to: r.entity.id
		}),
		polity: r.one.polity({
			from: r.office.polityId,
			to: r.polity.id
		}),
		statewideOfficeYears: r.many.statewideOfficeYear(),
		tenures: r.many.tenure(),
	},
	seat: {
		contests: r.many.contest(),
		areaApportionedAreaId: r.one.area({
			from: r.seat.apportionedAreaId,
			to: r.area.id,
			alias: "seat_apportionedAreaId_area_id"
		}),
		body: r.one.body({
			from: r.seat.bodyId,
			to: r.body.id
		}),
		changeset: r.one.changeset({
			from: r.seat.changesetId,
			to: r.changeset.id
		}),
		areaDistrictAreaId: r.one.area({
			from: r.seat.districtAreaId,
			to: r.area.id,
			alias: "seat_districtAreaId_area_id"
		}),
		entity: r.one.entity({
			from: r.seat.id,
			to: r.entity.id
		}),
		tenures: r.many.tenure(),
	},
	coverageGovernmentYear: {
		coverageGovernment: r.one.coverageGovernment({
			from: r.coverageGovernmentYear.governmentId,
			to: r.coverageGovernment.id
		}),
	},
	coverageGovernment: {
		coverageGovernmentYears: r.many.coverageGovernmentYear(),
	},
	crosswalk: {
		changeset: r.one.changeset({
			from: r.crosswalk.changesetId,
			to: r.changeset.id
		}),
		boundarySetFromBoundarySetId: r.one.boundarySet({
			from: r.crosswalk.fromBoundarySetId,
			to: r.boundarySet.id,
			alias: "crosswalk_fromBoundarySetId_boundarySet_id"
		}),
		scheme: r.one.scheme({
			from: r.crosswalk.fromSchemeCode,
			to: r.scheme.code
		}),
		boundarySetToBoundarySetId: r.one.boundarySet({
			from: r.crosswalk.toBoundarySetId,
			to: r.boundarySet.id,
			alias: "crosswalk_toBoundarySetId_boundarySet_id"
		}),
		crosswalkWeights: r.many.crosswalkWeight(),
	},
	crosswalkWeight: {
		crosswalk: r.one.crosswalk({
			from: r.crosswalkWeight.crosswalkId,
			to: r.crosswalk.id
		}),
	},
	entityMerge: {
		changeset: r.one.changeset({
			from: r.entityMerge.changesetId,
			to: r.changeset.id
		}),
		entityEntityId: r.one.entity({
			from: r.entityMerge.entityId,
			to: r.entity.id,
			alias: "entityMerge_entityId_entity_id"
		}),
		entityIntoId: r.one.entity({
			from: r.entityMerge.intoId,
			to: r.entity.id,
			alias: "entityMerge_intoId_entity_id"
		}),
	},
	factRekey: {
		changeset: r.one.changeset({
			from: r.factRekey.changesetId,
			to: r.changeset.id
		}),
	},
	financeSummary: {
		changeset: r.one.changeset({
			from: r.financeSummary.changesetId,
			to: r.changeset.id
		}),
		election: r.one.election({
			from: r.financeSummary.electionId,
			to: r.election.id
		}),
		source: r.one.source({
			from: r.financeSummary.sourceId,
			to: r.source.id
		}),
		entity: r.one.entity({
			from: r.financeSummary.subjectEntityId,
			to: r.entity.id
		}),
	},
	flag: {
		changeset: r.one.changeset({
			from: r.flag.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.flag.entityId,
			to: r.entity.id
		}),
		resultSet: r.one.resultSet({
			from: r.flag.resultSetId,
			to: r.resultSet.id
		}),
	},
	resultSet: {
		flags: r.many.flag(),
		outcomes: r.many.outcome(),
		changeset: r.one.changeset({
			from: r.resultSet.changesetId,
			to: r.changeset.id
		}),
		contest: r.one.contest({
			from: r.resultSet.contestId,
			to: r.contest.id
		}),
		election: r.one.election({
			from: r.resultSet.electionId,
			to: r.election.id
		}),
		entity: r.one.entity({
			from: r.resultSet.id,
			to: r.entity.id
		}),
		source: r.one.source({
			from: r.resultSet.sourceId,
			to: r.source.id
		}),
		resultSet: r.one.resultSet({
			from: r.resultSet.supersedesId,
			to: r.resultSet.id,
			alias: "resultSet_supersedesId_resultSet_id"
		}),
		resultSets: r.many.resultSet({
			alias: "resultSet_supersedesId_resultSet_id"
		}),
	},
	government: {
		changeset: r.one.changeset({
			from: r.government.changesetId,
			to: r.changeset.id
		}),
		election: r.one.election({
			from: r.government.electionId,
			to: r.election.id
		}),
		person: r.one.person({
			from: r.government.headPersonId,
			to: r.person.id
		}),
		entity: r.one.entity({
			from: r.government.id,
			to: r.entity.id
		}),
		office: r.one.office({
			from: r.government.officeId,
			to: r.office.id
		}),
		polity: r.one.polity({
			from: r.government.polityId,
			to: r.polity.id
		}),
		government: r.one.government({
			from: r.government.previousGovernmentId,
			to: r.government.id,
			alias: "government_previousGovernmentId_government_id"
		}),
		governments: r.many.government({
			alias: "government_previousGovernmentId_government_id"
		}),
		source: r.one.source({
			from: r.government.sourceId,
			to: r.source.id
		}),
		governmentParties: r.many.governmentParty(),
	},
	governmentParty: {
		changeset: r.one.changeset({
			from: r.governmentParty.changesetId,
			to: r.changeset.id
		}),
		government: r.one.government({
			from: r.governmentParty.governmentId,
			to: r.government.id
		}),
		party: r.one.party({
			from: r.governmentParty.partyId,
			to: r.party.id
		}),
	},
	indicatorValue: {
		indicator: r.one.indicator({
			from: r.indicatorValue.indicatorId,
			to: r.indicator.id
		}),
	},
	indicator: {
		indicatorValues: r.many.indicatorValue(),
	},
	label: {
		changeset: r.one.changeset({
			from: r.label.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.label.entityId,
			to: r.entity.id
		}),
		source: r.one.source({
			from: r.label.sourceId,
			to: r.source.id
		}),
	},
	membershipDrop: {
		changeset: r.one.changeset({
			from: r.membershipDrop.changesetId,
			to: r.changeset.id
		}),
		party: r.one.party({
			from: r.membershipDrop.partyId,
			to: r.party.id
		}),
		person: r.one.person({
			from: r.membershipDrop.personId,
			to: r.person.id
		}),
	},
	name: {
		changeset: r.one.changeset({
			from: r.name.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.name.entityId,
			to: r.entity.id
		}),
		source: r.one.source({
			from: r.name.sourceId,
			to: r.source.id
		}),
	},
	outcome: {
		candidacy: r.one.candidacy({
			from: r.outcome.candidacyId,
			to: r.candidacy.id
		}),
		changeset: r.one.changeset({
			from: r.outcome.changesetId,
			to: r.changeset.id
		}),
		contestOption: r.one.contestOption({
			from: r.outcome.optionId,
			to: r.contestOption.id
		}),
		resultSet: r.one.resultSet({
			from: r.outcome.resultSetId,
			to: r.resultSet.id
		}),
	},
	partyAffiliation: {
		changeset: r.one.changeset({
			from: r.partyAffiliation.changesetId,
			to: r.changeset.id
		}),
		partyParentPartyId: r.one.party({
			from: r.partyAffiliation.parentPartyId,
			to: r.party.id,
			alias: "partyAffiliation_parentPartyId_party_id"
		}),
		partyPartyId: r.one.party({
			from: r.partyAffiliation.partyId,
			to: r.party.id,
			alias: "partyAffiliation_partyId_party_id"
		}),
	},
	partyColor: {
		changeset: r.one.changeset({
			from: r.partyColor.changesetId,
			to: r.changeset.id
		}),
		party: r.one.party({
			from: r.partyColor.partyId,
			to: r.party.id
		}),
		source: r.one.source({
			from: r.partyColor.sourceId,
			to: r.source.id
		}),
	},
	partyPosition: {
		changeset: r.one.changeset({
			from: r.partyPosition.changesetId,
			to: r.changeset.id
		}),
		party: r.one.party({
			from: r.partyPosition.partyId,
			to: r.party.id
		}),
		source: r.one.source({
			from: r.partyPosition.sourceId,
			to: r.source.id
		}),
	},
	partyRelation: {
		changeset: r.one.changeset({
			from: r.partyRelation.changesetId,
			to: r.changeset.id
		}),
		election: r.one.election({
			from: r.partyRelation.electionId,
			to: r.election.id
		}),
		partyFromPartyId: r.one.party({
			from: r.partyRelation.fromPartyId,
			to: r.party.id,
			alias: "partyRelation_fromPartyId_party_id"
		}),
		source: r.one.source({
			from: r.partyRelation.sourceId,
			to: r.source.id
		}),
		partyToPartyId: r.one.party({
			from: r.partyRelation.toPartyId,
			to: r.party.id,
			alias: "partyRelation_toPartyId_party_id"
		}),
	},
	personImage: {
		changeset: r.one.changeset({
			from: r.personImage.changesetId,
			to: r.changeset.id
		}),
		person: r.one.person({
			from: r.personImage.personId,
			to: r.person.id
		}),
		source: r.one.source({
			from: r.personImage.sourceId,
			to: r.source.id
		}),
	},
	personPartyMembership: {
		changeset: r.one.changeset({
			from: r.personPartyMembership.changesetId,
			to: r.changeset.id
		}),
		party: r.one.party({
			from: r.personPartyMembership.partyId,
			to: r.party.id
		}),
		person: r.one.person({
			from: r.personPartyMembership.personId,
			to: r.person.id
		}),
		source: r.one.source({
			from: r.personPartyMembership.sourceId,
			to: r.source.id
		}),
	},
	polityParent: {
		changeset: r.one.changeset({
			from: r.polityParent.changesetId,
			to: r.changeset.id
		}),
		polityParentId: r.one.polity({
			from: r.polityParent.parentId,
			to: r.polity.id,
			alias: "polityParent_parentId_polity_id"
		}),
		polityPolityId: r.one.polity({
			from: r.polityParent.polityId,
			to: r.polity.id,
			alias: "polityParent_polityId_polity_id"
		}),
	},
	polityRelation: {
		changeset: r.one.changeset({
			from: r.polityRelation.changesetId,
			to: r.changeset.id
		}),
		polityFromPolityId: r.one.polity({
			from: r.polityRelation.fromPolityId,
			to: r.polity.id,
			alias: "polityRelation_fromPolityId_polity_id"
		}),
		source: r.one.source({
			from: r.polityRelation.sourceId,
			to: r.source.id
		}),
		polityToPolityId: r.one.polity({
			from: r.polityRelation.toPolityId,
			to: r.polity.id,
			alias: "polityRelation_toPolityId_polity_id"
		}),
	},
	polityTerritory: {
		area: r.one.area({
			from: r.polityTerritory.areaId,
			to: r.area.id
		}),
		changeset: r.one.changeset({
			from: r.polityTerritory.changesetId,
			to: r.changeset.id
		}),
		polity: r.one.polity({
			from: r.polityTerritory.polityId,
			to: r.polity.id
		}),
	},
	release: {
		changeset: r.one.changeset({
			from: r.release.watermarkChangesetId,
			to: r.changeset.id
		}),
	},
	reportingUnit: {
		area: r.one.area({
			from: r.reportingUnit.areaId,
			to: r.area.id
		}),
		changeset: r.one.changeset({
			from: r.reportingUnit.changesetId,
			to: r.changeset.id
		}),
		reportingUnitCountedWithId: r.one.reportingUnit({
			from: r.reportingUnit.countedWithId,
			to: r.reportingUnit.id,
			alias: "reportingUnit_countedWithId_reportingUnit_id"
		}),
		reportingUnitsCountedWithId: r.many.reportingUnit({
			alias: "reportingUnit_countedWithId_reportingUnit_id"
		}),
		entity: r.one.entity({
			from: r.reportingUnit.id,
			to: r.entity.id
		}),
		reportingUnitParentId: r.one.reportingUnit({
			from: r.reportingUnit.parentId,
			to: r.reportingUnit.id,
			alias: "reportingUnit_parentId_reportingUnit_id"
		}),
		reportingUnitsParentId: r.many.reportingUnit({
			alias: "reportingUnit_parentId_reportingUnit_id"
		}),
		polity: r.one.polity({
			from: r.reportingUnit.polityId,
			to: r.polity.id
		}),
		scheme: r.one.scheme({
			from: r.reportingUnit.schemeCode,
			to: r.scheme.code
		}),
		reportingUnitAreas: r.many.reportingUnitArea(),
	},
	reportingUnitArea: {
		area: r.one.area({
			from: r.reportingUnitArea.areaId,
			to: r.area.id
		}),
		changeset: r.one.changeset({
			from: r.reportingUnitArea.changesetId,
			to: r.changeset.id
		}),
		reportingUnit: r.one.reportingUnit({
			from: r.reportingUnitArea.unitId,
			to: r.reportingUnit.id
		}),
	},
	representationGap: {
		area: r.one.area({
			from: r.representationGap.areaId,
			to: r.area.id
		}),
		body: r.one.body({
			from: r.representationGap.bodyId,
			to: r.body.id
		}),
		changeset: r.one.changeset({
			from: r.representationGap.changesetId,
			to: r.changeset.id
		}),
	},
	sourceArtifact: {
		changesets: r.many.changeset({
			from: r.sourceArtifact.id.through(r.sourcePage.artifactId),
			to: r.changeset.id.through(r.sourcePage.changesetId)
		}),
	},
	statewideOfficeYear: {
		office: r.one.office({
			from: r.statewideOfficeYear.officeId,
			to: r.office.id
		}),
	},
	tenure: {
		body: r.one.body({
			from: r.tenure.bodyId,
			to: r.body.id
		}),
		changeset: r.one.changeset({
			from: r.tenure.changesetId,
			to: r.changeset.id
		}),
		contest: r.one.contest({
			from: r.tenure.contestId,
			to: r.contest.id
		}),
		entity: r.one.entity({
			from: r.tenure.id,
			to: r.entity.id
		}),
		office: r.one.office({
			from: r.tenure.officeId,
			to: r.office.id
		}),
		person: r.one.person({
			from: r.tenure.personId,
			to: r.person.id
		}),
		tenureResumesTenureId: r.one.tenure({
			from: r.tenure.resumesTenureId,
			to: r.tenure.id,
			alias: "tenure_resumesTenureId_tenure_id"
		}),
		tenuresResumesTenureId: r.many.tenure({
			alias: "tenure_resumesTenureId_tenure_id"
		}),
		seat: r.one.seat({
			from: r.tenure.seatId,
			to: r.seat.id
		}),
		tenureSucceedsTenureId: r.one.tenure({
			from: r.tenure.succeedsTenureId,
			to: r.tenure.id,
			alias: "tenure_succeedsTenureId_tenure_id"
		}),
		tenuresSucceedsTenureId: r.many.tenure({
			alias: "tenure_succeedsTenureId_tenure_id"
		}),
		tenureAffiliations: r.many.tenureAffiliation(),
		tenureEvents: r.many.tenureEvent(),
	},
	tenureAffiliation: {
		changeset: r.one.changeset({
			from: r.tenureAffiliation.changesetId,
			to: r.changeset.id
		}),
		party: r.one.party({
			from: r.tenureAffiliation.partyId,
			to: r.party.id
		}),
		tenure: r.one.tenure({
			from: r.tenureAffiliation.tenureId,
			to: r.tenure.id
		}),
	},
	tenureEvent: {
		changeset: r.one.changeset({
			from: r.tenureEvent.changesetId,
			to: r.changeset.id
		}),
		source: r.one.source({
			from: r.tenureEvent.sourceId,
			to: r.source.id
		}),
		tenure: r.one.tenure({
			from: r.tenureEvent.tenureId,
			to: r.tenure.id
		}),
	},
	sourcePage: {
		sources: r.many.source({
			from: r.sourcePage.id.through(r.valueLocator.pageId),
			to: r.source.id.through(r.valueLocator.sourceId)
		}),
	},
}))