import type PartyCard from "#lib/components/party-card.svelte";
import type PersonCard from "#lib/components/person-card.svelte";

export const cards = $state<{
  party: typeof PartyCard | null;
  person: typeof PersonCard | null;
}>({ party: null, person: null });

let loading: Promise<void> | undefined;

export function loadCards() {
  loading ??= Promise.all([
    import("#lib/components/party-card.svelte"),
    import("#lib/components/person-card.svelte"),
  ])
    .then(([party, person]) => {
      cards.party = party.default;
      cards.person = person.default;
    })
    .catch(() => {
      loading = undefined;
    });
}
