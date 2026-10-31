import { defineRelations } from "drizzle-orm";
import * as schema from "./core.ts";

export const relations = defineRelations(schema, (r) => ({
	activity: {
		user: r.one.users({
			from: r.activity.actorUserId,
			to: r.users.id
		}),
		apikey: r.one.apikeys({
			from: r.activity.agentKeyId,
			to: r.apikeys.id
		}),
		changeset: r.one.changeset({
			from: r.activity.changesetId,
			to: r.changeset.id
		}),
		comment: r.one.comment({
			from: r.activity.commentId,
			to: r.comment.id
		}),
		proposal: r.one.proposal({
			from: r.activity.proposalId,
			to: r.proposal.id
		}),
		thread: r.one.thread({
			from: r.activity.threadId,
			to: r.thread.id
		}),
	},
	users: {
		activities: r.many.activity(),
		archiveCaptures: r.many.archiveCapture(),
		changesetsAuthorUserId: r.many.changeset({
			alias: "changeset_authorUserId_users_id"
		}),
		changesetsReviewedBy: r.many.changeset({
			alias: "changeset_reviewedBy_users_id"
		}),
		commentsAuthorUserId: r.many.comment({
			alias: "comment_authorUserId_users_id"
		}),
		commentsHiddenBy: r.many.comment({
			alias: "comment_hiddenBy_users_id"
		}),
		profiles: r.many.profile(),
		proposalsAuthorUserId: r.many.proposal({
			alias: "proposal_authorUserId_users_id"
		}),
		proposalsDecidedBy: r.many.proposal({
			alias: "proposal_decidedBy_users_id"
		}),
		proposalsViaProposalJob: r.many.proposal({
			alias: "proposal_id_users_id_via_proposalJob"
		}),
		proposalJobs: r.many.proposalJob(),
		proposalsViaThread: r.many.proposal({
			from: r.users.id.through(r.thread.authorUserId),
			to: r.proposal.id.through(r.thread.proposalId),
			alias: "users_id_proposal_id_via_thread"
		}),
		threads: r.many.thread(),
		apikeys: r.many.apikeys(),
		uploads: r.many.upload(),
		watches: r.many.watch(),
	},
	apikeys: {
		activities: r.many.activity(),
		comments: r.many.comment(),
		proposals: r.many.proposal(),
		users: r.many.users({
			from: r.apikeys.id.through(r.upload.agentKeyId),
			to: r.users.id.through(r.upload.ownerUserId)
		}),
		uploads: r.many.upload(),
	},
	changeset: {
		activities: r.many.activity(),
		alliances: r.many.alliance(),
		allianceMembers: r.many.allianceMember(),
		areasChangesetId: r.many.area({
			alias: "area_changesetId_changeset_id"
		}),
		areasViaAreaCode: r.many.area({
			alias: "area_id_changeset_id_via_areaCode"
		}),
		areaCodes: r.many.areaCode(),
		sourcesViaAreaEvent: r.many.source({
			from: r.changeset.id.through(r.areaEvent.changesetId),
			to: r.source.id.through(r.areaEvent.sourceId),
			alias: "changeset_id_source_id_via_areaEvent"
		}),
		areaEvents: r.many.areaEvent(),
		areaEventMembers: r.many.areaEventMember(),
		areaGeometries: r.many.areaGeometry(),
		areaParents: r.many.areaParent(),
		areaUniverses: r.many.areaUniverse(),
		contestsViaBallotQuestion: r.many.contest({
			from: r.changeset.id.through(r.ballotQuestion.changesetId),
			to: r.contest.id.through(r.ballotQuestion.contestId),
			alias: "changeset_id_contest_id_via_ballotQuestion"
		}),
		ballotQuestions: r.many.ballotQuestion(),
		ballotQuestionTexts: r.many.ballotQuestionText(),
		bodiesChangesetId: r.many.body({
			alias: "body_changesetId_changeset_id"
		}),
		bodyApportionments: r.many.bodyApportionment(),
		bodyChambers: r.many.bodyChamber(),
		bodyEvents: r.many.bodyEvent(),
		bodiesViaBodySize: r.many.body({
			alias: "body_id_changeset_id_via_bodySize"
		}),
		bodySizes: r.many.bodySize(),
		bodiesViaBodySizePart: r.many.body({
			alias: "body_id_changeset_id_via_bodySizePart"
		}),
		bodySizeParts: r.many.bodySizePart(),
		schemesViaBoundarySet: r.many.scheme({
			from: r.changeset.id.through(r.boundarySet.changesetId),
			to: r.scheme.code.through(r.boundarySet.schemeCode),
			alias: "changeset_id_scheme_code_via_boundarySet"
		}),
		boundarySets: r.many.boundarySet(),
		candidacies: r.many.candidacy(),
		candidacyNominations: r.many.candidacyNomination(),
		userAuthorUserId: r.one.users({
			from: r.changeset.authorUserId,
			to: r.users.id,
			alias: "changeset_authorUserId_users_id"
		}),
		userReviewedBy: r.one.users({
			from: r.changeset.reviewedBy,
			to: r.users.id,
			alias: "changeset_reviewedBy_users_id"
		}),
		citations: r.many.citation(),
		contestsChangesetId: r.many.contest({
			alias: "contest_changesetId_changeset_id"
		}),
		contestOptions: r.many.contestOption(),
		crosswalks: r.many.crosswalk(),
		elections: r.many.election(),
		electoralSystems: r.many.electoralSystem(),
		entitiesChangesetId: r.many.entity({
			alias: "entity_changesetId_changeset_id"
		}),
		entitiesRetiredBy: r.many.entity({
			alias: "entity_retiredBy_changeset_id"
		}),
		entityMerges: r.many.entityMerge(),
		entitiesViaEntityRetirement: r.many.entity({
			from: r.changeset.id.through(r.entityRetirement.changesetId),
			to: r.entity.id.through(r.entityRetirement.entityId),
			alias: "changeset_id_entity_id_via_entityRetirement"
		}),
		entityRetirements: r.many.entityRetirement(),
		entitiesViaEntitySubtypeDrop: r.many.entity({
			from: r.changeset.id.through(r.entitySubtypeDrop.changesetId),
			to: r.entity.id.through(r.entitySubtypeDrop.entityId),
			alias: "changeset_id_entity_id_via_entitySubtypeDrop"
		}),
		entitySubtypeDrops: r.many.entitySubtypeDrop(),
		entitiesViaExternalId: r.many.entity({
			from: r.changeset.id.through(r.externalId.changesetId),
			to: r.entity.id.through(r.externalId.entityId),
			alias: "changeset_id_entity_id_via_externalId"
		}),
		externalIds: r.many.externalId(),
		entitiesViaExternalIdDrop: r.many.entity({
			from: r.changeset.id.through(r.externalIdDrop.changesetId),
			to: r.entity.id.through(r.externalIdDrop.entityId),
			alias: "changeset_id_entity_id_via_externalIdDrop"
		}),
		externalIdDrops: r.many.externalIdDrop(),
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
		indicators: r.many.indicator(),
		labels: r.many.label(),
		membershipDrops: r.many.membershipDrop(),
		names: r.many.name(),
		entitiesViaNameDrop: r.many.entity({
			from: r.changeset.id.through(r.nameDrop.changesetId),
			to: r.entity.id.through(r.nameDrop.entityId),
			alias: "changeset_id_entity_id_via_nameDrop"
		}),
		nameDrops: r.many.nameDrop(),
		entitiesViaNote: r.many.entity({
			from: r.changeset.id.through(r.note.changesetId),
			to: r.entity.id.through(r.note.entityId),
			alias: "changeset_id_entity_id_via_note"
		}),
		notes: r.many.note(),
		entitiesViaNoteDrop: r.many.entity({
			from: r.changeset.id.through(r.noteDrop.changesetId),
			to: r.entity.id.through(r.noteDrop.entityId),
			alias: "changeset_id_entity_id_via_noteDrop"
		}),
		noteDrops: r.many.noteDrop(),
		offices: r.many.office(),
		contestsViaOptionDrop: r.many.contest({
			from: r.changeset.id.through(r.optionDrop.changesetId),
			to: r.contest.id.through(r.optionDrop.contestId),
			alias: "changeset_id_contest_id_via_optionDrop"
		}),
		optionDrops: r.many.optionDrop(),
		outcomes: r.many.outcome(),
		parties: r.many.party(),
		partyAffiliations: r.many.partyAffiliation(),
		partyColors: r.many.partyColor(),
		partyFoundings: r.many.partyFounding(),
		partyImages: r.many.partyImage(),
		partyPositions: r.many.partyPosition(),
		partyRelations: r.many.partyRelation(),
		entitiesViaPerson: r.many.entity({
			from: r.changeset.id.through(r.person.changesetId),
			to: r.entity.id.through(r.person.id),
			alias: "changeset_id_entity_id_via_person"
		}),
		people: r.many.person(),
		personImages: r.many.personImage(),
		personPartyMemberships: r.many.personPartyMembership(),
		entitiesViaPolity: r.many.entity({
			from: r.changeset.id.through(r.polity.changesetId),
			to: r.entity.id.through(r.polity.id),
			alias: "changeset_id_entity_id_via_polity"
		}),
		politiesChangesetId: r.many.polity({
			alias: "polity_changesetId_changeset_id"
		}),
		polityParents: r.many.polityParent(),
		politiesViaPolityPeriod: r.many.polity({
			from: r.changeset.id.through(r.polityPeriod.changesetId),
			to: r.polity.id.through(r.polityPeriod.polityId),
			alias: "changeset_id_polity_id_via_polityPeriod"
		}),
		polityPeriods: r.many.polityPeriod(),
		polityRelations: r.many.polityRelation(),
		polityTerritories: r.many.polityTerritory(),
		proposalsHeadChangesetId: r.many.proposal({
			alias: "proposal_headChangesetId_changeset_id"
		}),
		proposalsViaProposalRevision: r.many.proposal({
			from: r.changeset.id.through(r.proposalRevision.changesetId),
			to: r.proposal.id.through(r.proposalRevision.proposalId),
			alias: "changeset_id_proposal_id_via_proposalRevision"
		}),
		proposalRevisions: r.one.proposalRevision(),
		releases: r.many.release(),
		reportingUnits: r.many.reportingUnit(),
		reportingUnitAreas: r.many.reportingUnitArea(),
		representationGaps: r.many.representationGap(),
		resultSets: r.many.resultSet(),
		resultSetDrops: r.many.resultSetDrop(),
		rowDrops: r.many.rowDrop(),
		politiesViaScheme: r.many.polity({
			from: r.changeset.id.through(r.scheme.changesetId),
			to: r.polity.id.through(r.scheme.polityId),
			alias: "changeset_id_polity_id_via_scheme"
		}),
		schemesChangesetId: r.many.scheme({
			alias: "scheme_changesetId_changeset_id"
		}),
		seats: r.many.seat(),
		entitiesViaSlug: r.many.entity({
			from: r.changeset.id.through(r.slug.changesetId),
			to: r.entity.id.through(r.slug.entityId),
			alias: "changeset_id_entity_id_via_slug"
		}),
		slugs: r.many.slug(),
		entitiesViaSource: r.many.entity({
			from: r.changeset.id.through(r.source.changesetId),
			to: r.entity.id.through(r.source.id),
			alias: "changeset_id_entity_id_via_source"
		}),
		sourcesChangesetId: r.many.source({
			alias: "source_changesetId_changeset_id"
		}),
		sourcesViaSourceArtifact: r.many.source({
			from: r.changeset.id.through(r.sourceArtifact.changesetId),
			to: r.source.id.through(r.sourceArtifact.sourceId),
			alias: "changeset_id_source_id_via_sourceArtifact"
		}),
		sourceArtifactsChangesetId: r.many.sourceArtifact({
			alias: "sourceArtifact_changesetId_changeset_id"
		}),
		sourceArtifactsViaSourcePage: r.many.sourceArtifact({
			alias: "sourceArtifact_id_changeset_id_via_sourcePage"
		}),
		sourcePages: r.many.sourcePage(),
		tenures: r.many.tenure(),
		tenureAffiliations: r.many.tenureAffiliation(),
		tenureEvents: r.many.tenureEvent(),
		sourcesViaTranslation: r.many.source({
			from: r.changeset.id.through(r.translation.changesetId),
			to: r.source.id.through(r.translation.sourceId),
			alias: "changeset_id_source_id_via_translation"
		}),
		translations: r.many.translation(),
		unitAreaDrops: r.many.unitAreaDrop(),
	},
	comment: {
		activities: r.many.activity(),
		apikey: r.one.apikeys({
			from: r.comment.agentKeyId,
			to: r.apikeys.id
		}),
		userAuthorUserId: r.one.users({
			from: r.comment.authorUserId,
			to: r.users.id,
			alias: "comment_authorUserId_users_id"
		}),
		userHiddenBy: r.one.users({
			from: r.comment.hiddenBy,
			to: r.users.id,
			alias: "comment_hiddenBy_users_id"
		}),
		comment: r.one.comment({
			from: r.comment.parentId,
			to: r.comment.id,
			alias: "comment_parentId_comment_id"
		}),
		comments: r.many.comment({
			alias: "comment_parentId_comment_id"
		}),
		thread: r.one.thread({
			from: r.comment.threadId,
			to: r.thread.id
		}),
	},
	proposal: {
		activities: r.many.activity(),
		apikey: r.one.apikeys({
			from: r.proposal.agentKeyId,
			to: r.apikeys.id
		}),
		userAuthorUserId: r.one.users({
			from: r.proposal.authorUserId,
			to: r.users.id,
			alias: "proposal_authorUserId_users_id"
		}),
		userDecidedBy: r.one.users({
			from: r.proposal.decidedBy,
			to: r.users.id,
			alias: "proposal_decidedBy_users_id"
		}),
		changeset: r.one.changeset({
			from: r.proposal.headChangesetId,
			to: r.changeset.id,
			alias: "proposal_headChangesetId_changeset_id"
		}),
		polity: r.one.polity({
			from: r.proposal.polityId,
			to: r.polity.id
		}),
		usersViaProposalJob: r.many.users({
			from: r.proposal.id.through(r.proposalJob.proposalId),
			to: r.users.id.through(r.proposalJob.requestedBy),
			alias: "proposal_id_users_id_via_proposalJob"
		}),
		proposalJobs: r.many.proposalJob(),
		changesets: r.many.changeset({
			alias: "changeset_id_proposal_id_via_proposalRevision"
		}),
		proposalRevisions: r.many.proposalRevision(),
		usersViaThread: r.many.users({
			alias: "users_id_proposal_id_via_thread"
		}),
		threads: r.one.thread(),
	},
	thread: {
		activities: r.many.activity(),
		comments: r.many.comment(),
		user: r.one.users({
			from: r.thread.authorUserId,
			to: r.users.id
		}),
		proposal: r.one.proposal({
			from: r.thread.proposalId,
			to: r.proposal.id
		}),
	},
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
		changesetChangesetId: r.one.changeset({
			from: r.entity.changesetId,
			to: r.changeset.id,
			alias: "entity_changesetId_changeset_id"
		}),
		changesetRetiredBy: r.one.changeset({
			from: r.entity.retiredBy,
			to: r.changeset.id,
			alias: "entity_retiredBy_changeset_id"
		}),
		entityMergesEntityId: r.many.entityMerge({
			alias: "entityMerge_entityId_entity_id"
		}),
		entityMergesIntoId: r.many.entityMerge({
			alias: "entityMerge_intoId_entity_id"
		}),
		changesetsViaEntityRetirement: r.many.changeset({
			alias: "changeset_id_entity_id_via_entityRetirement"
		}),
		entityRetirements: r.many.entityRetirement(),
		changesetsViaEntitySubtypeDrop: r.many.changeset({
			alias: "changeset_id_entity_id_via_entitySubtypeDrop"
		}),
		entitySubtypeDrops: r.many.entitySubtypeDrop(),
		changesetsViaExternalId: r.many.changeset({
			alias: "changeset_id_entity_id_via_externalId"
		}),
		externalIds: r.many.externalId(),
		changesetsViaExternalIdDrop: r.many.changeset({
			alias: "changeset_id_entity_id_via_externalIdDrop"
		}),
		externalIdDrops: r.many.externalIdDrop(),
		financeSummaries: r.many.financeSummary(),
		flags: r.many.flag(),
		governments: r.many.government(),
		labels: r.many.label(),
		names: r.many.name(),
		changesetsViaNameDrop: r.many.changeset({
			alias: "changeset_id_entity_id_via_nameDrop"
		}),
		nameDrops: r.many.nameDrop(),
		changesetsViaNote: r.many.changeset({
			alias: "changeset_id_entity_id_via_note"
		}),
		notes: r.many.note(),
		changesetsViaNoteDrop: r.many.changeset({
			alias: "changeset_id_entity_id_via_noteDrop"
		}),
		noteDrops: r.many.noteDrop(),
		offices: r.many.office(),
		parties: r.many.party(),
		changesetsViaPerson: r.many.changeset({
			alias: "changeset_id_entity_id_via_person"
		}),
		people: r.many.person(),
		changesetsViaPolity: r.many.changeset({
			alias: "changeset_id_entity_id_via_polity"
		}),
		politiesId: r.many.polity({
			alias: "polity_id_entity_id"
		}),
		reportingUnits: r.many.reportingUnit(),
		resultSets: r.many.resultSet(),
		politiesViaSearchDocument: r.many.polity({
			from: r.entity.id.through(r.searchDocument.entityId),
			to: r.polity.id.through(r.searchDocument.polityId),
			alias: "entity_id_polity_id_via_searchDocument"
		}),
		searchDocuments: r.many.searchDocument(),
		seats: r.many.seat(),
		changesetsViaSlug: r.many.changeset({
			alias: "changeset_id_entity_id_via_slug"
		}),
		slugs: r.one.slug(),
		changesetsViaSource: r.many.changeset({
			alias: "changeset_id_entity_id_via_source"
		}),
		sources: r.many.source(),
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
		partyFoundings: r.many.partyFounding(),
		partyImages: r.many.partyImage(),
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
	archiveCapture: {
		user: r.one.users({
			from: r.archiveCapture.ownerUserId,
			to: r.users.id
		}),
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
		areaCodes: r.many.areaCode(),
		areaEventMembers: r.many.areaEventMember(),
		areaGeometries: r.many.areaGeometry(),
		areaParentsAreaId: r.many.areaParent({
			alias: "areaParent_areaId_area_id"
		}),
		areaParentsParentId: r.many.areaParent({
			alias: "areaParent_parentId_area_id"
		}),
		areaUniverses: r.many.areaUniverse(),
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
		unitAreaDrops: r.many.unitAreaDrop(),
	},
	scheme: {
		areas: r.many.area(),
		changesets: r.many.changeset({
			alias: "changeset_id_scheme_code_via_boundarySet"
		}),
		boundarySets: r.many.boundarySet(),
		crosswalks: r.many.crosswalk(),
		reportingUnits: r.many.reportingUnit(),
		changeset: r.one.changeset({
			from: r.scheme.changesetId,
			to: r.changeset.id,
			alias: "scheme_changesetId_changeset_id"
		}),
		polity: r.one.polity({
			from: r.scheme.polityId,
			to: r.polity.id
		}),
	},
	areaCode: {
		area: r.one.area({
			from: r.areaCode.areaId,
			to: r.area.id
		}),
		changeset: r.one.changeset({
			from: r.areaCode.changesetId,
			to: r.changeset.id
		}),
	},
	source: {
		changesetsViaAreaEvent: r.many.changeset({
			alias: "changeset_id_source_id_via_areaEvent"
		}),
		areaEvents: r.many.areaEvent(),
		areaGeometries: r.many.areaGeometry(),
		areaUniverses: r.many.areaUniverse(),
		ballotQuestionTexts: r.many.ballotQuestionText(),
		bodyChambers: r.many.bodyChamber(),
		bodyEvents: r.many.bodyEvent(),
		citations: r.many.citation(),
		financeSummaries: r.many.financeSummary(),
		governments: r.many.government(),
		changesetsViaIndicator: r.many.changeset({
			alias: "changeset_id_source_id_via_indicator"
		}),
		indicators: r.many.indicator(),
		labels: r.many.label(),
		names: r.many.name(),
		partyColors: r.many.partyColor(),
		partyFoundings: r.many.partyFounding(),
		partyImages: r.many.partyImage(),
		partyPositions: r.many.partyPosition(),
		partyRelations: r.many.partyRelation(),
		personImages: r.many.personImage(),
		personPartyMemberships: r.many.personPartyMembership(),
		polityRelations: r.many.polityRelation(),
		resultSets: r.many.resultSet(),
		changeset: r.one.changeset({
			from: r.source.changesetId,
			to: r.changeset.id,
			alias: "source_changesetId_changeset_id"
		}),
		entity: r.one.entity({
			from: r.source.id,
			to: r.entity.id
		}),
		changesetsViaSourceArtifact: r.many.changeset({
			alias: "changeset_id_source_id_via_sourceArtifact"
		}),
		sourceArtifacts: r.many.sourceArtifact(),
		tenureEvents: r.many.tenureEvent(),
		changesetsViaTranslation: r.many.changeset({
			alias: "changeset_id_source_id_via_translation"
		}),
		translations: r.many.translation(),
		sourcePages: r.many.sourcePage(),
		valueLocators: r.many.valueLocator(),
	},
	areaEvent: {
		changeset: r.one.changeset({
			from: r.areaEvent.changesetId,
			to: r.changeset.id
		}),
		source: r.one.source({
			from: r.areaEvent.sourceId,
			to: r.source.id
		}),
		areaEventMembers: r.many.areaEventMember(),
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
	areaUniverse: {
		area: r.one.area({
			from: r.areaUniverse.areaId,
			to: r.area.id
		}),
		changeset: r.one.changeset({
			from: r.areaUniverse.changesetId,
			to: r.changeset.id
		}),
		resultSet: r.one.resultSet({
			from: r.areaUniverse.resultSetId,
			to: r.resultSet.id
		}),
		source: r.one.source({
			from: r.areaUniverse.sourceId,
			to: r.source.id
		}),
	},
	resultSet: {
		areaUniverses: r.many.areaUniverse(),
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
	contest: {
		changesetsViaBallotQuestion: r.many.changeset({
			alias: "changeset_id_contest_id_via_ballotQuestion"
		}),
		ballotQuestions: r.many.ballotQuestion(),
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
		changesetsViaOptionDrop: r.many.changeset({
			alias: "changeset_id_contest_id_via_optionDrop"
		}),
		optionDrops: r.many.optionDrop(),
		resultSets: r.many.resultSet(),
		tenures: r.many.tenure(),
	},
	ballotQuestion: {
		changeset: r.one.changeset({
			from: r.ballotQuestion.changesetId,
			to: r.changeset.id
		}),
		contest: r.one.contest({
			from: r.ballotQuestion.contestId,
			to: r.contest.id
		}),
		ballotQuestionTexts: r.many.ballotQuestionText(),
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
		bodyChambersBodyId: r.many.bodyChamber({
			alias: "bodyChamber_bodyId_body_id"
		}),
		bodyChambersOfBodyId: r.many.bodyChamber({
			alias: "bodyChamber_ofBodyId_body_id"
		}),
		bodyEvents: r.many.bodyEvent(),
		changesetsViaBodySize: r.many.changeset({
			from: r.body.id.through(r.bodySize.bodyId),
			to: r.changeset.id.through(r.bodySize.changesetId),
			alias: "body_id_changeset_id_via_bodySize"
		}),
		bodySizes: r.many.bodySize(),
		changesetsViaBodySizePart: r.many.changeset({
			from: r.body.id.through(r.bodySizePart.bodyId),
			to: r.changeset.id.through(r.bodySizePart.changesetId),
			alias: "body_id_changeset_id_via_bodySizePart"
		}),
		bodySizeParts: r.many.bodySizePart(),
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
		changeset: r.one.changeset({
			from: r.polity.changesetId,
			to: r.changeset.id,
			alias: "polity_changesetId_changeset_id"
		}),
		entity: r.one.entity({
			from: r.polity.id,
			to: r.entity.id,
			alias: "polity_id_entity_id"
		}),
		polityParentsParentId: r.many.polityParent({
			alias: "polityParent_parentId_polity_id"
		}),
		polityParentsPolityId: r.many.polityParent({
			alias: "polityParent_polityId_polity_id"
		}),
		changesetsViaPolityPeriod: r.many.changeset({
			alias: "changeset_id_polity_id_via_polityPeriod"
		}),
		polityPeriods: r.many.polityPeriod(),
		polityRelationsFromPolityId: r.many.polityRelation({
			alias: "polityRelation_fromPolityId_polity_id"
		}),
		polityRelationsToPolityId: r.many.polityRelation({
			alias: "polityRelation_toPolityId_polity_id"
		}),
		polityTerritories: r.many.polityTerritory(),
		proposals: r.many.proposal(),
		reportingUnits: r.many.reportingUnit(),
		changesetsViaScheme: r.many.changeset({
			alias: "changeset_id_polity_id_via_scheme"
		}),
		schemes: r.many.scheme(),
		entities: r.many.entity({
			alias: "entity_id_polity_id_via_searchDocument"
		}),
		searchDocuments: r.many.searchDocument(),
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
	bodyChamber: {
		bodyBodyId: r.one.body({
			from: r.bodyChamber.bodyId,
			to: r.body.id,
			alias: "bodyChamber_bodyId_body_id"
		}),
		changeset: r.one.changeset({
			from: r.bodyChamber.changesetId,
			to: r.changeset.id
		}),
		bodyOfBodyId: r.one.body({
			from: r.bodyChamber.ofBodyId,
			to: r.body.id,
			alias: "bodyChamber_ofBodyId_body_id"
		}),
		source: r.one.source({
			from: r.bodyChamber.sourceId,
			to: r.source.id
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
	bodySize: {
		body: r.one.body({
			from: r.bodySize.bodyId,
			to: r.body.id
		}),
		changeset: r.one.changeset({
			from: r.bodySize.changesetId,
			to: r.changeset.id
		}),
	},
	bodySizePart: {
		body: r.one.body({
			from: r.bodySizePart.bodyId,
			to: r.body.id
		}),
		changeset: r.one.changeset({
			from: r.bodySizePart.changesetId,
			to: r.changeset.id
		}),
	},
	boundarySet: {
		changeset: r.one.changeset({
			from: r.boundarySet.changesetId,
			to: r.changeset.id
		}),
		scheme: r.one.scheme({
			from: r.boundarySet.schemeCode,
			to: r.scheme.code
		}),
		contests: r.many.contest(),
		crosswalksFromBoundarySetId: r.many.crosswalk({
			alias: "crosswalk_fromBoundarySetId_boundarySet_id"
		}),
		crosswalksToBoundarySetId: r.many.crosswalk({
			alias: "crosswalk_toBoundarySetId_boundarySet_id"
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
		changeset: r.one.changeset({
			from: r.person.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.person.id,
			to: r.entity.id
		}),
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
	entityRetirement: {
		changeset: r.one.changeset({
			from: r.entityRetirement.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.entityRetirement.entityId,
			to: r.entity.id
		}),
	},
	entitySubtypeDrop: {
		changeset: r.one.changeset({
			from: r.entitySubtypeDrop.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.entitySubtypeDrop.entityId,
			to: r.entity.id
		}),
	},
	externalId: {
		changeset: r.one.changeset({
			from: r.externalId.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.externalId.entityId,
			to: r.entity.id
		}),
	},
	externalIdDrop: {
		changeset: r.one.changeset({
			from: r.externalIdDrop.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.externalIdDrop.entityId,
			to: r.entity.id
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
	indicator: {
		changeset: r.one.changeset({
			from: r.indicator.changesetId,
			to: r.changeset.id
		}),
		source: r.one.source({
			from: r.indicator.sourceId,
			to: r.source.id
		}),
		indicatorValues: r.many.indicatorValue(),
	},
	indicatorValue: {
		indicator: r.one.indicator({
			from: r.indicatorValue.indicatorId,
			to: r.indicator.id
		}),
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
	nameDrop: {
		changeset: r.one.changeset({
			from: r.nameDrop.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.nameDrop.entityId,
			to: r.entity.id
		}),
	},
	note: {
		changeset: r.one.changeset({
			from: r.note.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.note.entityId,
			to: r.entity.id
		}),
	},
	noteDrop: {
		changeset: r.one.changeset({
			from: r.noteDrop.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.noteDrop.entityId,
			to: r.entity.id
		}),
	},
	optionDrop: {
		changeset: r.one.changeset({
			from: r.optionDrop.changesetId,
			to: r.changeset.id
		}),
		contest: r.one.contest({
			from: r.optionDrop.contestId,
			to: r.contest.id
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
	partyFounding: {
		changeset: r.one.changeset({
			from: r.partyFounding.changesetId,
			to: r.changeset.id
		}),
		party: r.one.party({
			from: r.partyFounding.partyId,
			to: r.party.id
		}),
		source: r.one.source({
			from: r.partyFounding.sourceId,
			to: r.source.id
		}),
	},
	partyImage: {
		changeset: r.one.changeset({
			from: r.partyImage.changesetId,
			to: r.changeset.id
		}),
		party: r.one.party({
			from: r.partyImage.partyId,
			to: r.party.id
		}),
		source: r.one.source({
			from: r.partyImage.sourceId,
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
	polityPeriod: {
		changeset: r.one.changeset({
			from: r.polityPeriod.changesetId,
			to: r.changeset.id
		}),
		polity: r.one.polity({
			from: r.polityPeriod.polityId,
			to: r.polity.id
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
	profile: {
		user: r.one.users({
			from: r.profile.userId,
			to: r.users.id
		}),
	},
	proposalJob: {
		proposal: r.one.proposal({
			from: r.proposalJob.proposalId,
			to: r.proposal.id
		}),
		user: r.one.users({
			from: r.proposalJob.requestedBy,
			to: r.users.id
		}),
	},
	proposalRevision: {
		changeset: r.one.changeset({
			from: r.proposalRevision.changesetId,
			to: r.changeset.id
		}),
		proposal: r.one.proposal({
			from: r.proposalRevision.proposalId,
			to: r.proposal.id
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
		unitAreaDrops: r.many.unitAreaDrop(),
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
	resultSetDrop: {
		changeset: r.one.changeset({
			from: r.resultSetDrop.changesetId,
			to: r.changeset.id
		}),
	},
	rowDrop: {
		changeset: r.one.changeset({
			from: r.rowDrop.changesetId,
			to: r.changeset.id
		}),
	},
	searchDocument: {
		entity: r.one.entity({
			from: r.searchDocument.entityId,
			to: r.entity.id
		}),
		polity: r.one.polity({
			from: r.searchDocument.polityId,
			to: r.polity.id
		}),
	},
	slug: {
		changeset: r.one.changeset({
			from: r.slug.changesetId,
			to: r.changeset.id
		}),
		entity: r.one.entity({
			from: r.slug.entityId,
			to: r.entity.id
		}),
	},
	sourceArtifact: {
		changeset: r.one.changeset({
			from: r.sourceArtifact.changesetId,
			to: r.changeset.id,
			alias: "sourceArtifact_changesetId_changeset_id"
		}),
		source: r.one.source({
			from: r.sourceArtifact.sourceId,
			to: r.source.id
		}),
		changesets: r.many.changeset({
			from: r.sourceArtifact.id.through(r.sourcePage.artifactId),
			to: r.changeset.id.through(r.sourcePage.changesetId),
			alias: "sourceArtifact_id_changeset_id_via_sourcePage"
		}),
		sourcePages: r.many.sourcePage(),
	},
	sourcePage: {
		sourceArtifact: r.one.sourceArtifact({
			from: r.sourcePage.artifactId,
			to: r.sourceArtifact.id
		}),
		changeset: r.one.changeset({
			from: r.sourcePage.changesetId,
			to: r.changeset.id
		}),
		sources: r.many.source({
			from: r.sourcePage.id.through(r.valueLocator.pageId),
			to: r.source.id.through(r.valueLocator.sourceId)
		}),
		valueLocators: r.many.valueLocator(),
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
	translation: {
		changeset: r.one.changeset({
			from: r.translation.changesetId,
			to: r.changeset.id
		}),
		source: r.one.source({
			from: r.translation.sourceId,
			to: r.source.id
		}),
	},
	unitAreaDrop: {
		area: r.one.area({
			from: r.unitAreaDrop.areaId,
			to: r.area.id
		}),
		changeset: r.one.changeset({
			from: r.unitAreaDrop.changesetId,
			to: r.changeset.id
		}),
		reportingUnit: r.one.reportingUnit({
			from: r.unitAreaDrop.unitId,
			to: r.reportingUnit.id
		}),
	},
	upload: {
		apikey: r.one.apikeys({
			from: r.upload.agentKeyId,
			to: r.apikeys.id
		}),
		user: r.one.users({
			from: r.upload.ownerUserId,
			to: r.users.id
		}),
		uploadPages: r.many.uploadPage(),
	},
	uploadPage: {
		upload: r.one.upload({
			from: r.uploadPage.uploadId,
			to: r.upload.id
		}),
	},
	valueLocator: {
		sourcePage: r.one.sourcePage({
			from: r.valueLocator.pageId,
			to: r.sourcePage.id
		}),
		source: r.one.source({
			from: r.valueLocator.sourceId,
			to: r.source.id
		}),
	},
	watch: {
		user: r.one.users({
			from: r.watch.userId,
			to: r.users.id
		}),
	},
}))