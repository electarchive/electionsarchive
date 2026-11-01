import type { Project } from "#lib/projects.js";
import { m } from "#lib/paraglide/messages.js";
import { PROJECTS } from "#lib/projects.js";

const UPDATED = "2026-11-01";
const SLUGS = new Set<string>(PROJECTS);

type Section = { id: string; title: string; body: string; quote?: string };

export function isProject(slug: string): slug is Project {
  return SLUGS.has(slug);
}

export function projectTitle(slug: Project) {
  return {
    about: m.about_title,
    architecture: m.architecture_title,
    license: m.license_title,
    contribute: m.contribute_title,
    privacy: m.privacy_title,
    terms: m.terms_title,
  }[slug]();
}

function sections(slug: Project): Array<Section> {
  switch (slug) {
    case "about": {
      return [
        {
          id: "holds",
          title: m.about_holds_title(),
          body: m.about_holds_body(),
        },
        {
          id: "promises",
          title: m.about_promises_title(),
          body: m.about_promises_body(),
        },
        {
          id: "preview",
          title: m.about_status_title(),
          body: m.about_status_body(),
        },
        {
          id: "maker",
          title: m.about_maker_title(),
          body: m.about_maker_body(),
        },
        {
          id: "contact",
          title: m.about_contact_title(),
          body: m.about_contact_body(),
        },
      ];
    }
    case "architecture": {
      return [
        {
          id: "database",
          title: m.architecture_database_title(),
          body: m.architecture_database_body(),
        },
        {
          id: "in",
          title: m.architecture_in_title(),
          body: m.architecture_in_body(),
        },
        {
          id: "out",
          title: m.architecture_out_title(),
          body: m.architecture_out_body(),
        },
        {
          id: "maps",
          title: m.architecture_maps_title(),
          body: m.architecture_maps_body(),
        },
        {
          id: "live",
          title: m.architecture_live_title(),
          body: m.architecture_live_body(),
        },
        {
          id: "search",
          title: m.architecture_search_title(),
          body: m.architecture_search_body(),
        },
        {
          id: "hosting",
          title: m.architecture_hosting_title(),
          body: m.architecture_hosting_body(),
        },
        {
          id: "code",
          title: m.architecture_code_title(),
          body: m.architecture_code_body(),
        },
      ];
    }
    case "license": {
      return [
        {
          id: "data",
          title: m.license_data_title(),
          body: m.license_data_body(),
        },
        {
          id: "sources",
          title: m.license_sources_title(),
          body: m.license_sources_body(),
        },
        {
          id: "ownership",
          title: m.license_ownership_title(),
          body: m.license_ownership_body(),
        },
        {
          id: "cite",
          title: m.license_cite_title(),
          body: m.license_cite_body(),
          quote: m.license_cite_example(),
        },
        {
          id: "contributions",
          title: m.license_contributions_title(),
          body: m.license_contributions_body(),
        },
        {
          id: "code",
          title: m.license_code_title(),
          body: m.license_code_body(),
        },
      ];
    }
    case "contribute": {
      return [
        {
          id: "today",
          title: m.contribute_today_title(),
          body: m.contribute_today_body(),
        },
        {
          id: "how",
          title: m.contribute_how_title(),
          body: m.contribute_how_body(),
        },
        {
          id: "agents",
          title: m.contribute_agents_title(),
          body: m.contribute_agents_body(),
        },
        {
          id: "limits",
          title: m.contribute_limits_title(),
          body: m.contribute_limits_body(),
        },
      ];
    }
    case "privacy": {
      return [
        {
          id: "reading",
          title: m.privacy_reading_title(),
          body: m.privacy_reading_body(),
        },
        {
          id: "network",
          title: m.privacy_network_title(),
          body: m.privacy_network_body(),
        },
        {
          id: "accounts",
          title: m.privacy_accounts_title(),
          body: m.privacy_accounts_body(),
        },
        {
          id: "contributions",
          title: m.privacy_contributions_title(),
          body: m.privacy_contributions_body(),
        },
        {
          id: "updates",
          title: m.privacy_newsletter_title(),
          body: m.privacy_newsletter_body(),
        },
        {
          id: "people",
          title: m.privacy_people_title(),
          body: m.privacy_people_body(),
        },
        {
          id: "choices",
          title: m.privacy_rights_title(),
          body: m.privacy_rights_body(),
        },
      ];
    }
    case "terms": {
      return [
        { id: "use", title: m.terms_use_title(), body: m.terms_use_body() },
        { id: "api", title: m.terms_api_title(), body: m.terms_api_body() },
        {
          id: "accuracy",
          title: m.terms_accuracy_title(),
          body: m.terms_accuracy_body(),
        },
        {
          id: "accounts",
          title: m.terms_accounts_title(),
          body: m.terms_accounts_body(),
        },
        {
          id: "contributions",
          title: m.terms_contributions_title(),
          body: m.terms_contributions_body(),
        },
        {
          id: "liability",
          title: m.terms_liability_title(),
          body: m.terms_liability_body(),
        },
        {
          id: "changes",
          title: m.terms_changes_title(),
          body: m.terms_changes_body(),
        },
      ];
    }
  }
}

export function project(slug: Project) {
  return {
    slug,
    title: projectTitle(slug),
    lede: {
      about: m.about_lede,
      architecture: m.architecture_lede,
      license: m.license_lede,
      contribute: m.contribute_lede,
      privacy: m.privacy_lede,
      terms: m.terms_lede,
    }[slug](),
    updated: slug === "privacy" || slug === "terms" ? UPDATED : null,
    sections: sections(slug),
    pages: PROJECTS.map((entry) => ({
      slug: entry,
      title: projectTitle(entry),
    })),
  };
}
