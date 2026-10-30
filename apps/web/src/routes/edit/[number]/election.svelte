<script lang="ts">
  import PlusIcon from "@lucide/svelte/icons/plus";
  import XIcon from "@lucide/svelte/icons/x";
  import { tick, untrack } from "svelte";
  import type { ContestKind, Found, OptionKind } from "#lib/workspace.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import * as Command from "#lib/components/ui/command/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import { languages as tags } from "#lib/contrib.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import {
    contestKinds,
    electionKinds,
    isRef,
    languageItems,
    languageLabel,
    optionKinds,
    questionTypes,
    recordKind,
    templateTitle,
  } from "#lib/workspace.js";
  import type { PageData } from "./$types";
  import Cite from "./cite.svelte";
  import { Records } from "./records.svelte.js";
  import Ref from "./ref.svelte";
  import { Session } from "./session.svelte.js";
  import Shell from "./shell.svelte";

  const {
    data,
    workspace,
  }: {
    data: PageData;
    workspace: Extract<PageData["workspace"], { template: "election" }>;
  } = $props();

  type Option = {
    key: number;
    kind: string;
    person: string;
    label: string;
    party: string;
  };
  type Contest = {
    key: number;
    name: string;
    kind: string;
    question: string;
    area: string;
    office: string;
    body: string;
    magnitude: string;
    options: Array<Option>;
  };
  type Looked = {
    entity: Found | null;
    problem: string | null;
    isLooking: boolean;
  };

  const electionKindLabel: Record<
    (typeof electionKinds)[number],
    () => string
  > = {
    general: m.ws_ekind_general,
    by_election: m.ws_ekind_by_election,
    primary: m.ws_ekind_primary,
    runoff: m.ws_ekind_runoff,
    referendum: m.ws_ekind_referendum,
    recall: m.ws_ekind_recall,
    local: m.ws_ekind_local,
    special: m.ws_ekind_special,
  };
  const contestKindLabel: Record<ContestKind, () => string> = {
    office: m.ws_ckind_office,
    body_seats: m.ws_ckind_body_seats,
    list_allocation: m.ws_ckind_list_allocation,
    ballot_question: m.ws_ckind_ballot_question,
  };
  const questionLabel: Record<(typeof questionTypes)[number], () => string> = {
    initiative: m.ws_qtype_initiative,
    mandatory_referendum: m.ws_qtype_mandatory_referendum,
    optional_referendum: m.ws_qtype_optional_referendum,
    plebiscite: m.ws_qtype_plebiscite,
    recall_question: m.ws_qtype_recall_question,
  };
  const optionKindLabel: Record<OptionKind, () => string> = {
    candidate: m.ws_okind_candidate,
    write_in: m.ws_okind_write_in,
    others: m.ws_okind_others,
    list: m.ws_okind_list,
    answer: m.ws_okind_answer,
  };
  const diffTables = [
    ["election", m.ws_diff_table_election],
    ["contest", m.ws_diff_table_contest],
    ["contest_option", m.ws_diff_table_option],
    ["candidacy", m.ws_diff_table_candidacy],
    ["person", m.ws_diff_table_person],
  ] as const;

  const languages = languageItems();
  const none: Looked = { entity: null, problem: null, isLooking: false };

  const contestKindOf = (value: string): ContestKind =>
    contestKinds.find((kind) => kind === value) ?? "office";

  const optionKindOf = (contest: ContestKind, value: string): OptionKind => {
    const allowed: ReadonlyArray<OptionKind> = optionKinds[contest];
    return allowed.find((kind) => kind === value) ?? optionKinds[contest][0];
  };

  const slugOf = (text: string) =>
    text
      .normalize("NFKD")
      .replaceAll(/\p{M}/gu, "")
      .toLowerCase()
      .replaceAll(/[^\p{L}\p{N}]+/gu, "-")
      .replaceAll(/^-|-$/gu, "");

  const isDate = (text: string) =>
    /^\d{4}-\d{2}-\d{2}$/u.test(text) &&
    !Number.isNaN(Date.parse(text)) &&
    new Date(`${text}T00:00:00Z`).toISOString().startsWith(text);

  const now = new Date();
  const today = [
    String(now.getFullYear()).padStart(4, "0"),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");

  let next = 0;
  const option = (kind = "candidate", person = "", label = "", party = "") => ({
    key: next++,
    kind,
    person,
    label,
    party,
  });
  const answers = () => [option("answer"), option("answer")];

  const initial = untrack(() => ({ ...data, workspace }));
  const election = $state({ ...initial.workspace.election });
  let contests = $state<Array<Contest>>(
    initial.workspace.contests.map((contest) => ({
      ...contest,
      key: next++,
      options: contest.options.map((item) =>
        option(item.kind, item.person, item.label, item.party),
      ),
    })),
  );
  let summary = $state(initial.summary);
  let cited = $state(initial.workspace.evidence);
  const records = new Records(initial.records, () => [
    election.polity,
    election.parent,
    ...contests.flatMap((contest) => [
      contest.area,
      contest.office,
      contest.body,
      ...contest.options.flatMap((item) => [
        isRef(item.person) ? item.person : "",
        item.party,
      ]),
    ]),
  ]);
  let selected = $state<number | null>(null);

  const look = (value: string, kind: string): Looked => {
    if (value.trim() === "") {
      return none;
    }
    const found = records.get(value);
    if (found == null) {
      return { entity: null, problem: null, isLooking: true };
    }
    if (found.state === "missing") {
      return { entity: null, problem: m.ws_record_missing(), isLooking: false };
    }
    if (found.state === "retired") {
      return { entity: null, problem: m.ws_record_retired(), isLooking: false };
    }
    if (found.type !== kind) {
      return {
        entity: null,
        problem: m.ws_ref_wrong_kind({
          wanted: recordKind(kind),
          kind: recordKind(found.type),
        }),
        isLooking: false,
      };
    }
    return { entity: found, problem: null, isLooking: false };
  };

  const assessed = $derived.by(() => {
    const day = election.date.trim();
    const slugs: Record<string, number> = {};
    for (const [index, contest] of contests.entries()) {
      const tail = slugOf(contest.name);
      if (tail !== "" && slugs[tail] == null) {
        slugs[tail] = index;
      }
    }
    return {
      polity: look(election.polity, "polity"),
      parent: look(election.parent, "election"),
      day,
      isDated: isDate(day),
      dateProblem: day !== "" && !isDate(day) ? m.ws_election_bad_date() : null,
      contests: contests.map((contest, index) => {
        const kind = contestKindOf(contest.kind);
        const tail = slugOf(contest.name);
        const seats = contest.magnitude.trim();
        const isSeated = kind === "body_seats" || kind === "list_allocation";
        const magnitude =
          !isSeated || seats === ""
            ? null
            : /^\d+$/u.test(seats) && Number(seats) >= 1
              ? Number(seats)
              : Number.NaN;
        const question = questionTypes.find(
          (type) => type === contest.question,
        );
        const options = contest.options.map((item) => {
          const sort = optionKindOf(kind, item.kind);
          const person = item.person.trim();
          const label = item.label.trim();
          const party = item.party.trim();
          const isNew = sort === "candidate" && person !== "" && !isRef(person);
          const isBlank =
            (sort === "candidate" && person === "" && party === "") ||
            (sort === "list" && party === "" && label === "") ||
            (sort === "answer" && label === "");
          const looked = {
            person:
              sort === "candidate" && !isNew ? look(person, "person") : none,
            party:
              sort === "candidate" || sort === "list"
                ? look(party, "party")
                : none,
          };
          const needs =
            isBlank || looked.person.problem != null
              ? null
              : sort === "candidate" && person === ""
                ? m.ws_option_needs_person()
                : sort === "list" && party === ""
                  ? m.ws_option_needs_party()
                  : null;
          const states = {
            person: {
              ...looked.person,
              problem: looked.person.problem ?? needs,
            },
            party: {
              ...looked.party,
              problem: looked.party.problem ?? (sort === "list" ? needs : null),
            },
          };
          return {
            item,
            sort,
            isBlank,
            isNew,
            person,
            label,
            states,
            isLooking: looked.person.isLooking || looked.party.isLooking,
            problem: isBlank
              ? null
              : (states.person.problem ?? states.party.problem),
            op: {
              kind: sort,
              ...(sort === "candidate" &&
                (isNew
                  ? { person_name: person }
                  : looked.person.entity != null && {
                      person: looked.person.entity.id,
                    })),
              ...(label !== "" && { label }),
              ...(looked.party.entity != null && {
                party: looked.party.entity.id,
              }),
            },
          };
        });
        const refs = {
          area: look(contest.area, "area"),
          office: kind === "office" ? look(contest.office, "office") : none,
          body: isSeated ? look(contest.body, "body") : none,
        };
        const nameProblem =
          contest.name.trim() === "" || tail === ""
            ? m.ws_contest_needs_name()
            : slugs[tail] === index
              ? null
              : m.ws_contest_same_name();
        const questionProblem =
          kind === "ballot_question" && question == null
            ? m.ws_contest_needs_question()
            : null;
        const seatsProblem = Number.isNaN(magnitude)
          ? m.ws_contest_bad_seats()
          : null;
        const problem =
          nameProblem ??
          questionProblem ??
          seatsProblem ??
          refs.area.problem ??
          refs.office.problem ??
          refs.body.problem ??
          options.find((entry) => entry.problem != null)?.problem ??
          null;
        return {
          contest,
          n: index + 1,
          kind,
          isSeated,
          refs,
          options,
          kept: options.filter((entry) => !entry.isBlank),
          nameProblem,
          questionProblem,
          seatsProblem,
          problem,
          isLooking:
            refs.area.isLooking ||
            refs.office.isLooking ||
            refs.body.isLooking ||
            options.some((entry) => entry.isLooking),
          op: {
            name: contest.name.trim(),
            kind,
            ...(kind === "ballot_question" && question != null && { question }),
            ...(refs.area.entity != null && { area: refs.area.entity.id }),
            ...(refs.office.entity != null && {
              office: refs.office.entity.id,
            }),
            ...(refs.body.entity != null && { body: refs.body.entity.id }),
            ...(magnitude != null && !Number.isNaN(magnitude) && { magnitude }),
            options: options
              .filter((entry) => !entry.isBlank)
              .map((entry) => entry.op),
          },
        };
      }),
    };
  });

  const status = $derived(
    assessed.isDated
      ? assessed.day <= today
        ? ("held" as const)
        : ("scheduled" as const)
      : null,
  );

  const source = $derived.by(() => {
    if (cited == null) {
      return null;
    }
    const url = cited.url.trim();
    const title = cited.title.trim();
    const publisher = cited.publisher.trim();
    return /^https?:\/\//iu.test(url) &&
      URL.canParse(url) &&
      title !== "" &&
      publisher !== ""
      ? { url, title, publisher, kind: cited.kind }
      : null;
  });

  const hasProblem = $derived(
    assessed.polity.problem != null ||
      assessed.parent.problem != null ||
      assessed.dateProblem != null ||
      assessed.contests.some((item) => item.problem != null),
  );

  const isLookingUp = $derived(
    assessed.polity.isLooking ||
      assessed.parent.isLooking ||
      assessed.contests.some((item) => item.isLooking),
  );

  const ops = $derived.by(() => {
    const polity = assessed.polity.entity;
    const lang = tags.find((tag) => tag === election.lang);
    const kind = electionKinds.find((item) => item === election.kind);
    if (
      polity == null ||
      status == null ||
      lang == null ||
      kind == null ||
      election.name.trim() === "" ||
      contests.length === 0 ||
      source == null ||
      hasProblem ||
      isLookingUp
    ) {
      return [];
    }
    return [
      {
        op: "election" as const,
        election: {
          polity: polity.id,
          ...(assessed.parent.entity != null && {
            parent: assessed.parent.entity.id,
          }),
          kind,
          status,
          date: assessed.day,
          name: election.name.trim(),
          lang,
          contests: assessed.contests.map((item) => item.op),
        },
        evidence: ["e1"] as ["e1"],
      },
    ];
  });

  const compiled = $derived(JSON.stringify([ops, source]));

  const serialized = $derived(
    JSON.stringify({
      summary: summary.trim(),
      ops,
      ...(source != null && { evidence: { e1: source } }),
      workspace: {
        template: "election",
        election,
        contests: contests.map(({ key: _key, options, ...contest }) => ({
          ...contest,
          options: options.map(({ key: _option, ...item }) => item),
        })),
        evidence: cited,
      },
    }),
  );

  const session = new Session(
    untrack(() => data.number),
    untrack(() => data.check),
    {
      serialized: () => serialized,
      compiled: () => compiled,
      canCheck: () => ops.length > 0,
    },
  );

  const check = $derived(session.check);
  const isLocked = $derived(session.isLocked);

  const failure = $derived.by(() => {
    const error = session.error;
    if (error == null) {
      return null;
    }
    const at = /contests\[(\d+)\]/u.exec(error.path);
    return { contest: at == null ? null : Number(at[1]) + 1, text: error.text };
  });

  const blocker = $derived(
    session.blocker(
      hasProblem || failure != null
        ? m.ws_submit_needs_fix()
        : election.polity.trim() === ""
          ? m.ws_election_needs_polity()
          : status == null
            ? m.ws_election_bad_date()
            : election.name.trim() === ""
              ? m.ws_election_needs_name()
              : contests.length === 0
                ? m.ws_submit_needs_election()
                : source == null
                  ? m.ws_election_evidence_needed()
                  : null,
      summary,
    ),
  );

  const created = $derived(
    diffTables.flatMap(([table, label]) => {
      const count = check?.checks?.diff?.[table]?.insert ?? 0;
      return count === 0 ? [] : [{ table, label, count }];
    }),
  );

  async function focus(id: string, key: number) {
    selected = key;
    await tick();
    document.getElementById(id)?.focus();
  }

  function pickContest(n: number) {
    const contest = contests[n - 1];
    if (contest != null) {
      void focus(`ws-contest-${contest.key}`, contest.key);
    }
  }

  function addContest() {
    const isQuestion = election.kind === "referendum";
    const contest: Contest = {
      key: next++,
      name: "",
      kind: isQuestion ? "ballot_question" : "office",
      question: "",
      area: "",
      office: "",
      body: "",
      magnitude: "",
      options: isQuestion ? answers() : [option()],
    };
    contests.push(contest);
    void focus(`ws-contest-${contest.key}`, contest.key);
  }

  function removeContest(key: number) {
    contests = contests.filter((contest) => contest.key !== key);
  }

  function setContestKind(contest: Contest, value: string) {
    const kind = contestKindOf(value);
    contest.kind = kind;
    const isEmpty = contest.options.every(
      (item) =>
        item.person.trim() === "" &&
        item.label.trim() === "" &&
        item.party.trim() === "",
    );
    contest.options =
      kind === "ballot_question" && isEmpty
        ? answers()
        : contest.options.map((item) => ({
            ...item,
            kind: optionKindOf(kind, item.kind),
          }));
  }

  function addOption(contest: Contest) {
    const item = option(optionKinds[contestKindOf(contest.kind)][0]);
    contest.options.push(item);
    void focus(`ws-option-${item.key}`, contest.key);
  }

  function addOptionSelected() {
    const contest = contests.find((item) => item.key === selected);
    if (contest != null) {
      addOption(contest);
    }
  }

  function removeOption(contest: Contest, key: number) {
    contest.options = contest.options.filter((item) => item.key !== key);
  }

  function paste(event: ClipboardEvent, contest: Contest, index: number) {
    const text = event.clipboardData?.getData("text/plain") ?? "";
    if (!/[\t\n]/u.test(text.trim())) {
      return;
    }
    event.preventDefault();
    const kind = contestKindOf(contest.kind);
    const sort = optionKinds[kind][0];
    const pasted: Array<Option> = [];
    for (const line of text.replaceAll("\r", "").split("\n")) {
      if (line.trim() === "") {
        continue;
      }
      const [first = "", second = ""] = (
        line.includes("\t") ? line.split("\t") : line.split(",")
      ).map((cell) => cell.trim().replace(/^"(.*)"$/u, "$1"));
      pasted.push(
        sort === "candidate"
          ? option(sort, first, "", second)
          : sort === "list"
            ? option(sort, "", second, first)
            : option(sort, "", first),
      );
    }
    const current = contest.options[index];
    const replaces =
      current?.person.trim() === "" &&
      current.label.trim() === "" &&
      current.party.trim() === "";
    contest.options.splice(index, replaces ? 1 : 0, ...pasted);
    contest.options = contest.options.slice(0, 500);
  }
</script>

{#snippet choice(
  id: string,
  label: string,
  value: string,
  items: ReadonlyArray<{ value: string; label: string }>,
  onchange: (next: string) => void,
  isLabelShown = true,
)}
  <div class="grid content-start gap-1 min-inline-0">
    <span
      id="{id}-label"
      class={cn("text-xs text-muted-foreground", { "sr-only": !isLabelShown })}
      >{label}</span
    >
    <Select.Root
      type="single"
      {value}
      disabled={isLocked}
      onValueChange={onchange}
    >
      <Select.Trigger
        {id}
        class="text-sm block-8 inline-full"
        aria-labelledby="{id}-label"
        >{items.find((item) => item.value === value)?.label ??
          ""}</Select.Trigger
      >
      <Select.Content>
        {#each items as item (item.value)}
          <Select.Item value={item.value}>{item.label}</Select.Item>
        {/each}
      </Select.Content>
    </Select.Root>
  </div>
{/snippet}

<Shell
  {session}
  number={data.number}
  heading={templateTitle.election()}
  bind:summary
  {blocker}
  failure={failure == null
    ? null
    : {
        label:
          failure.contest == null
            ? null
            : m.ws_contest_label({ n: failure.contest }),
        text: failure.text,
        pick:
          failure.contest == null
            ? null
            : () => pickContest(failure.contest ?? 1),
      }}
>
  {#snippet outline()}
    <div class="grid gap-2 px-2 pbe-3">
      <p class="px-2 font-semibold text-pretty">
        {election.name.trim() === "" ? templateTitle.election() : election.name}
      </p>
      {#if contests.length === 0}
        <p class="px-2 text-pretty text-muted-foreground">
          {m.ws_contest_add()}
        </p>
      {:else}
        <ol class="grid">
          {#each assessed.contests as item (item.contest.key)}
            {const isWrong = $derived(
              item.problem != null || failure?.contest === item.n,
            )}
            <li>
              <button
                type="button"
                class={cn(
                  "grid gap-0.5 rounded-sm px-2 py-1.5 text-start inline-full hover:bg-muted",
                  { "bg-muted": selected === item.contest.key },
                )}
                onclick={() => pickContest(item.n)}
              >
                <span class="flex items-baseline justify-between gap-2">
                  <span class="truncate font-semibold min-inline-0"
                    >{item.contest.name.trim() === ""
                      ? m.ws_contest_label({ n: item.n })
                      : item.contest.name}</span
                  >
                  {#if isWrong}
                    <span class="font-figures font-semibold text-destructive"
                      >{item.n}</span
                    >
                  {/if}
                </span>
                <span class="truncate text-xs text-muted-foreground"
                  >{contestKindLabel[item.kind]()}, {m.ws_outline_options({
                    count: item.kept.length,
                  })}</span
                >
              </button>
            </li>
          {/each}
        </ol>
      {/if}
    </div>
  {/snippet}

  {#snippet editor()}
    <section class="grid gap-3 p-4" aria-labelledby="ws-election">
      <h2 id="ws-election" class="font-semibold">{m.ws_election_title()}</h2>
      <div class="grid gap-x-4 gap-y-3 sm:grid-cols-2">
        <Ref
          bind:value={election.polity}
          id="ws-election-polity"
          label={m.ws_election_polity()}
          {isLocked}
          state={assessed.polity}
        />
        <div class="grid content-start gap-1">
          <label for="ws-election-date" class="text-xs text-muted-foreground"
            >{m.ws_election_date()}</label
          >
          <Input
            id="ws-election-date"
            bind:value={election.date}
            disabled={isLocked}
            placeholder="YYYY-MM-DD"
            inputmode="numeric"
            autocomplete="off"
            maxlength={10}
            aria-invalid={assessed.dateProblem != null}
            class="font-figures block-8"
          />
          {#if assessed.dateProblem != null}
            <p class="text-xs font-semibold text-destructive">
              {assessed.dateProblem}
            </p>
          {:else if status != null}
            <p class="text-xs text-pretty text-muted-foreground">
              {status === "held"
                ? m.ws_election_status_held()
                : m.ws_election_status_scheduled()}
            </p>
          {/if}
        </div>
        {@render choice(
          "ws-election-kind",
          m.ws_election_kind(),
          election.kind,
          electionKinds.map((kind) => ({
            value: kind,
            label: electionKindLabel[kind](),
          })),
          (next) => (election.kind = next),
        )}
        <div class="grid content-start gap-1 min-inline-0">
          <span id="ws-election-lang" class="text-xs text-muted-foreground"
            >{m.ws_election_lang()}</span
          >
          {#if isLocked}
            <Input
              value={languageLabel(election.lang)}
              disabled
              aria-labelledby="ws-election-lang"
              class="block-8"
            />
          {:else}
            <Combobox
              items={languages}
              value={election.lang}
              label={m.ws_election_lang()}
              placeholder={m.ws_election_lang()}
              search={m.edit_note_lang_search()}
              empty={m.edit_note_lang_empty()}
              class="block-8 inline-full"
              onValueChange={(chosen: string | null) => {
                election.lang = chosen ?? "en";
              }}
            />
          {/if}
        </div>
        <div class="grid content-start gap-1 sm:col-span-2">
          <label for="ws-election-name" class="text-xs text-muted-foreground"
            >{m.ws_election_name()}</label
          >
          <Input
            id="ws-election-name"
            bind:value={election.name}
            disabled={isLocked}
            placeholder={m.ws_election_name_placeholder()}
            maxlength={300}
            class="font-semibold block-8"
          />
        </div>
        <Ref
          bind:value={election.parent}
          id="ws-election-parent"
          label={m.ws_election_parent()}
          {isLocked}
          state={assessed.parent}
        />
      </div>
    </section>

    <section class="grid gap-3 border-bs p-4" aria-labelledby="ws-contests">
      <h2 id="ws-contests" class="font-semibold">{m.ws_contests_title()}</h2>
      <ol class="grid gap-6">
        {#each assessed.contests as item (item.contest.key)}
          {const contest = $derived(item.contest)}
          {const isFailed = $derived(failure?.contest === item.n)}
          <li
            class={cn("grid gap-3 border-bs-2 pbs-2", {
              "border-foreground": item.problem == null && !isFailed,
              "border-destructive": item.problem != null || isFailed,
            })}
            aria-label={m.ws_contest_label({ n: item.n })}
          >
            <div class="grid grid-cols-[2rem_minmax(0,1fr)_2rem] gap-x-2">
              <span
                class="pbs-1.5 text-end font-figures text-muted-foreground"
                aria-hidden="true">{item.n}</span
              >
              <div class="grid gap-1">
                <label
                  for="ws-contest-{contest.key}"
                  class="text-xs text-muted-foreground"
                  >{m.ws_contest_name()}</label
                >
                <Input
                  id="ws-contest-{contest.key}"
                  bind:value={contest.name}
                  disabled={isLocked}
                  placeholder={m.ws_contest_name_placeholder()}
                  maxlength={300}
                  aria-invalid={item.nameProblem != null &&
                    contest.name.trim() !== ""}
                  class="font-semibold block-8"
                  onfocus={() => (selected = contest.key)}
                />
                {#if item.nameProblem != null && contest.name.trim() !== ""}
                  <p class="text-xs font-semibold text-destructive">
                    {item.nameProblem}
                  </p>
                {/if}
              </div>
              <Button
                variant="ghost"
                size="icon"
                class="self-end text-muted-foreground block-8 inline-8"
                disabled={isLocked}
                aria-label={m.ws_contest_remove({ n: item.n })}
                title={m.ws_contest_remove({ n: item.n })}
                onclick={() => removeContest(contest.key)}><XIcon /></Button
              >
            </div>
            <div class="grid gap-x-4 gap-y-3 ps-10 sm:grid-cols-3">
              {@render choice(
                `ws-contest-kind-${contest.key}`,
                m.ws_contest_kind(),
                item.kind,
                contestKinds.map((kind) => ({
                  value: kind,
                  label: contestKindLabel[kind](),
                })),
                (next) => setContestKind(contest, next),
              )}
              {#if item.kind === "ballot_question"}
                <div class="grid content-start gap-1">
                  {@render choice(
                    `ws-contest-question-${contest.key}`,
                    m.ws_contest_question(),
                    contest.question,
                    questionTypes.map((type) => ({
                      value: type,
                      label: questionLabel[type](),
                    })),
                    (next) => (contest.question = next),
                  )}
                  {#if item.questionProblem != null}
                    <p class="text-xs text-muted-foreground">
                      {item.questionProblem}
                    </p>
                  {/if}
                </div>
              {:else if item.kind === "office"}
                <Ref
                  bind:value={contest.office}
                  id="ws-contest-office-{contest.key}"
                  label={m.ws_contest_office()}
                  {isLocked}
                  state={item.refs.office}
                />
              {:else}
                <Ref
                  bind:value={contest.body}
                  id="ws-contest-body-{contest.key}"
                  label={m.ws_contest_body()}
                  {isLocked}
                  state={item.refs.body}
                />
              {/if}
              <Ref
                bind:value={contest.area}
                id="ws-contest-area-{contest.key}"
                label={m.ws_contest_area()}
                {isLocked}
                state={item.refs.area}
              />
              {#if item.isSeated}
                <div class="grid content-start gap-1">
                  <label
                    for="ws-contest-seats-{contest.key}"
                    class="text-xs text-muted-foreground"
                    >{m.ws_contest_seats()}</label
                  >
                  <Input
                    id="ws-contest-seats-{contest.key}"
                    bind:value={contest.magnitude}
                    disabled={isLocked}
                    inputmode="numeric"
                    autocomplete="off"
                    maxlength={4}
                    aria-invalid={item.seatsProblem != null}
                    class="font-figures block-8 inline-24"
                  />
                  {#if item.seatsProblem != null}
                    <p class="text-xs font-semibold text-destructive">
                      {item.seatsProblem}
                    </p>
                  {/if}
                </div>
              {/if}
            </div>
            <div class="grid gap-1 ps-10">
              <div
                class="flex flex-wrap items-baseline justify-between gap-x-4"
              >
                <h3 class="text-sm font-semibold">{m.ws_options_title()}</h3>
                {#if item.kind !== "ballot_question"}
                  <span class="text-xs text-muted-foreground"
                    >{m.ws_options_hint()}</span
                  >
                {/if}
              </div>
              <ol class="grid border-bs">
                {#each item.options as entry, index (entry.item.key)}
                  {const label = $derived(
                    m.ws_option_label_row({ n: index + 1 }),
                  )}
                  {const allowed = $derived(optionKinds[item.kind])}
                  <li
                    class="grid grid-cols-[2rem_minmax(0,1fr)_2rem] gap-x-2 border-be py-1.5"
                  >
                    <span
                      class="pbs-1.5 text-end font-figures text-xs text-muted-foreground"
                      aria-hidden="true">{index + 1}</span
                    >
                    <div
                      class={cn("grid gap-2", {
                        "sm:grid-cols-[8rem_minmax(0,1fr)_minmax(0,1fr)]":
                          allowed.length > 1,
                      })}
                    >
                      {#if allowed.length > 1}
                        {@render choice(
                          `ws-option-kind-${entry.item.key}`,
                          `${label}, ${m.ws_option_kind()}`,
                          entry.sort,
                          allowed.map((kind) => ({
                            value: kind,
                            label: optionKindLabel[kind](),
                          })),
                          (next) => (entry.item.kind = next),
                          false,
                        )}
                      {/if}
                      {#if entry.sort === "candidate"}
                        <Ref
                          bind:value={entry.item.person}
                          id="ws-option-{entry.item.key}"
                          label="{label}, {m.ws_option_person()}"
                          isLabelShown={false}
                          placeholder={m.ws_option_person_placeholder()}
                          {isLocked}
                          state={entry.states.person}
                          note={entry.isNew
                            ? m.ws_option_new_person({ name: entry.person })
                            : null}
                          onfocus={() => (selected = contest.key)}
                          onpaste={(event: ClipboardEvent) =>
                            paste(event, contest, index)}
                        />
                        <Ref
                          bind:value={entry.item.party}
                          id="ws-option-party-{entry.item.key}"
                          label="{label}, {m.ws_option_party()}"
                          isLabelShown={false}
                          {isLocked}
                          state={entry.states.party}
                        />
                      {:else if entry.sort === "list"}
                        <Ref
                          bind:value={entry.item.party}
                          id="ws-option-{entry.item.key}"
                          label="{label}, {m.ws_option_party_list()}"
                          isLabelShown={false}
                          {isLocked}
                          state={entry.states.party}
                          onfocus={() => (selected = contest.key)}
                          onpaste={(event: ClipboardEvent) =>
                            paste(event, contest, index)}
                        />
                        {@render text(entry.item, label, m.ws_option_label())}
                      {:else}
                        {@render text(
                          entry.item,
                          label,
                          entry.sort === "answer"
                            ? m.ws_okind_answer()
                            : m.ws_option_label(),
                          entry.sort === "answer"
                            ? m.ws_option_answer_placeholder()
                            : optionKindLabel[entry.sort](),
                          `ws-option-${entry.item.key}`,
                        )}
                      {/if}
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      class="self-end text-muted-foreground block-8 inline-8"
                      disabled={isLocked}
                      aria-label={m.ws_option_remove({ n: index + 1 })}
                      onclick={() => removeOption(contest, entry.item.key)}
                      ><XIcon /></Button
                    >
                  </li>
                {/each}
              </ol>
              {#if !isLocked}
                <Button
                  variant="outline"
                  size="sm"
                  class="mbs-2 justify-self-start"
                  onclick={() => addOption(contest)}
                  ><PlusIcon />{m.ws_option_add()}</Button
                >
              {/if}
            </div>
          </li>
        {/each}
      </ol>
      {#if !isLocked}
        <Button
          variant="outline"
          size="sm"
          class="justify-self-start"
          onclick={addContest}><PlusIcon />{m.ws_contest_add()}</Button
        >
      {/if}
    </section>
  {/snippet}

  {#snippet diff()}
    {#if created.length === 0}
      <p class="text-pretty text-muted-foreground">{m.ws_diff_empty()}</p>
    {:else}
      {#if session.isStale}
        <p class="pbe-2 text-xs font-semibold">{m.ws_checks_stale()}</p>
      {/if}
      <section>
        <h3 class="text-xs text-muted-foreground">{m.ws_diff_created()}</h3>
        <dl class="mbs-1 grid border-s-2 border-foreground">
          {#each created as row (row.table)}
            <div
              class="flex items-baseline justify-between border-be py-1.5 ps-3"
            >
              <dt>{row.label()}</dt>
              <dd class="font-figures font-semibold">{row.count}</dd>
            </div>
          {/each}
        </dl>
      </section>
    {/if}
  {/snippet}

  {#snippet evidence()}
    <Cite bind:cited {isLocked} empty={m.ws_election_evidence_needed()} />
  {/snippet}

  {#snippet commands(act: (action: () => void) => void)}
    <Command.Item disabled={isLocked} onSelect={() => act(addContest)}
      >{m.ws_contest_add()}</Command.Item
    >
    <Command.Item
      disabled={isLocked || selected == null}
      onSelect={() => act(addOptionSelected)}>{m.ws_option_add()}</Command.Item
    >
  {/snippet}
</Shell>

{#snippet text(
  item: Option,
  row: string,
  label: string,
  placeholder = "",
  id = `ws-option-label-${item.key}`,
)}
  <div class="grid content-start gap-1 min-inline-0">
    <label for={id} class="sr-only">{row}, {label}</label>
    <Input
      {id}
      bind:value={item.label}
      disabled={isLocked}
      {placeholder}
      maxlength={300}
      class="block-8"
    />
  </div>
{/snippet}
