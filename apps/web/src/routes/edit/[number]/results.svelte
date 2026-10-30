<script lang="ts">
  import { resolve } from "$app/paths";
  import Columns3Icon from "@lucide/svelte/icons/columns-3";
  import PanelLeftCloseIcon from "@lucide/svelte/icons/panel-left-close";
  import PanelLeftOpenIcon from "@lucide/svelte/icons/panel-left-open";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import XIcon from "@lucide/svelte/icons/x";
  import { tick, untrack } from "svelte";
  import { SvelteMap, SvelteSet } from "svelte/reactivity";
  import type { Mark, Place, Region, Sheet, Upload } from "#lib/sheet.js";
  import UploadEvidence from "#lib/components/contrib/upload-evidence.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Command from "#lib/components/ui/command/index.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import {
    sourceKindLabel,
    sourceKinds,
    stageLabel,
    stages,
  } from "#lib/contrib.js";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import {
    columnLabel,
    columnOf,
    defaultColumns,
    figure,
    fold,
    measureKey,
    outcomeKinds,
    outcomeLabel,
    placeKey,
    splitTable,
    unitMeasures,
  } from "#lib/sheet.js";
  import { cn } from "#lib/utils.js";
  import { templateTitle } from "#lib/workspace.js";
  import type { PageData } from "./$types";
  import Scan from "./scan.svelte";
  import { Session } from "./session.svelte.js";
  import Shell from "./shell.svelte";

  const {
    data,
    workspace,
  }: {
    data: PageData;
    workspace: Extract<PageData["workspace"], { template: "results" }>;
  } = $props();

  type Row = {
    key: string;
    unit: Place;
    name: string;
    values: Record<string, string>;
    marks: Record<string, Mark>;
  };
  type Contest = {
    key: number;
    record: string;
    stage: string;
    columns: Array<string>;
    rows: Array<Row>;
    printed: Record<string, string>;
    outcomes: Record<string, string>;
  };
  type Out = {
    unit: Place;
    option: string | null;
    measure: string;
    channel: "all";
    count_round: 0;
    value: number;
    page?: number;
    region?: Region;
  };

  let next = 0;
  const blankContest = (record = ""): Contest => ({
    key: next++,
    record,
    stage: "certified",
    columns: [],
    rows: [],
    printed: {},
    outcomes: {},
  });

  const initial = untrack(() => ({ ...data, workspace }));
  const restored = structuredClone(initial.workspace.contests).map(
    (contest): Contest => ({
      ...contest,
      key: next++,
      rows: contest.rows.map((row) => ({ ...row, key: placeKey(row.unit) })),
    }),
  );
  const first = restored[0] ?? blankContest();

  let contests = $state<Array<Contest>>(
    restored.length === 0 ? [first] : restored,
  );
  let activeKey = $state(first.key);
  let summary = $state(initial.summary);
  const cited = $state(
    structuredClone(initial.workspace.evidence) ?? {
      url: "",
      upload: null,
      title: "",
      publisher: "",
      kind: "official_body" as const,
    },
  );
  let upload = $state<Upload | null>(initial.upload);
  const records = new SvelteMap<string, PageData["records"][string] | null>(
    Object.entries(initial.records),
  );
  const sheets = new SvelteMap<string, Sheet | null>(
    Object.entries(initial.sheets),
  );
  let cell = $state<{ row: string; column: string } | null>(null);
  let page = $state(1);
  let showScan = $state(true);
  let split = $state(46);
  let body = $state<HTMLDivElement | null>(null);
  let dragging = $state(false);
  let pasted = $state<{
    contest: number;
    rows: number;
    added: number;
    skipped: number;
    undo: Contest;
  } | null>(null);
  const place = $state({ scheme: "", code: "" });
  let checkMap = $state<
    Array<{ contest: number; cells: Array<{ row: string; column: string }> }>
  >([]);

  const source = $derived.by(() => {
    const title = cited.title.trim();
    const publisher = cited.publisher.trim();
    if (title === "" || publisher === "") {
      return null;
    }
    if (cited.upload != null && upload?.id === cited.upload) {
      return { upload: cited.upload, title, publisher, kind: cited.kind };
    }
    const url = cited.url.trim();
    return /^https?:\/\//iu.test(url) && URL.canParse(url)
      ? { url, title, publisher, kind: cited.kind }
      : null;
  });

  const scanned = $derived(source != null && "upload" in source);

  const assessed = $derived(
    contests.map((contest) => {
      const ref = contest.record.trim();
      const found = ref === "" ? undefined : records.get(ref);
      const entity =
        found?.state === "found" && found.type === "contest" ? found : null;
      const sheet = entity == null ? null : (sheets.get(entity.id) ?? null);
      const problem =
        found?.state === "missing"
          ? m.ws_record_missing()
          : found?.state === "retired"
            ? m.ws_record_retired()
            : found?.state === "found" && entity == null
              ? m.ws_results_not_contest()
              : null;
      const stage = stages.find((item) => item === contest.stage) ?? null;
      const cells: Array<{ row: string; column: string; out: Out }> = [];
      const totals = new SvelteMap<string, number>();
      const sums = new SvelteMap<string, number>();
      const mismatched = new SvelteSet<string>();
      const valid = measureKey("valid");
      const hasValid = contest.columns.includes(valid);
      let invalid = 0;
      let boxes = 0;
      let places = 0;
      for (const row of contest.rows) {
        let sum = 0;
        let hasOption = false;
        let filled = false;
        for (const column of contest.columns) {
          const value = figure(row.values[column] ?? "");
          const mark = row.marks[column];
          if (mark != null) {
            boxes += 1;
          }
          if (value == null) {
            continue;
          }
          if (Number.isNaN(value)) {
            invalid += 1;
            continue;
          }
          filled = true;
          totals.set(column, (totals.get(column) ?? 0) + value);
          const { option, measure } = columnOf(column);
          if (option != null) {
            sum += value;
            hasOption = true;
          }
          cells.push({
            row: row.key,
            column,
            out: {
              unit: row.unit,
              option,
              measure,
              channel: "all",
              count_round: 0,
              value,
              ...(scanned &&
                mark != null && { page: mark.page, region: mark.region }),
            },
          });
        }
        if (filled) {
          places += 1;
        }
        sums.set(row.key, sum);
        const stated = hasValid ? figure(row.values[valid] ?? "") : null;
        if (
          hasOption &&
          stated != null &&
          !Number.isNaN(stated) &&
          Math.abs(sum - stated) > 0.5
        ) {
          mismatched.add(row.key);
        }
      }
      const printedOff = new Set(
        contest.columns.filter((column) => {
          const printed = figure(contest.printed[column] ?? "");
          return (
            printed != null &&
            !Number.isNaN(printed) &&
            Math.abs((totals.get(column) ?? 0) - printed) > 0.5
          );
        }),
      );
      const outcomes = Object.entries(contest.outcomes).flatMap(
        ([option, kind]) => {
          const known = outcomeKinds.find((item) => item === kind);
          return known != null && contest.columns.includes(option)
            ? [{ option, kind: known }]
            : [];
        },
      );
      return {
        contest,
        entity,
        sheet,
        problem,
        stage,
        cells,
        totals,
        sums,
        mismatched,
        printedOff,
        outcomes,
        invalid,
        boxes,
        places,
        isLooking:
          (ref !== "" && found == null) || (entity != null && sheet == null),
      };
    }),
  );

  type Assessed = (typeof assessed)[number];

  const ready = $derived(
    assessed.flatMap((item) =>
      item.entity != null &&
      item.problem == null &&
      item.stage != null &&
      item.invalid === 0 &&
      item.cells.length > 0
        ? [{ ...item, id: item.entity.id, stage: item.stage }]
        : [],
    ),
  );

  const ops = $derived(
    ready.map((item) => ({
      op: "results" as const,
      contest_id: item.id,
      stage: item.stage,
      rows: item.cells.map((entry) => entry.out),
      ...(item.outcomes.length > 0 && { outcomes: item.outcomes }),
      ...(source != null && { evidence: ["e1"] }),
    })),
  );

  const compiled = $derived(JSON.stringify([ops, source]));

  const serialized = $derived(
    JSON.stringify({
      summary: summary.trim(),
      ops,
      ...(source != null && { evidence: { e1: source } }),
      workspace: {
        template: "results",
        contests: contests.map((contest) => ({
          record: contest.record,
          stage: contest.stage,
          columns: contest.columns,
          rows: contest.rows.map((row) => ({
            unit: row.unit,
            name: row.name,
            values: Object.fromEntries(
              Object.entries(row.values).filter(
                ([, value]) => value.trim() !== "",
              ),
            ),
            marks: row.marks,
          })),
          printed: Object.fromEntries(
            Object.entries(contest.printed).filter(
              ([, value]) => value.trim() !== "",
            ),
          ),
          outcomes: Object.fromEntries(
            Object.entries(contest.outcomes).filter(([, kind]) => kind !== ""),
          ),
        })),
        evidence: cited,
      },
    }),
  );

  const isPending = $derived(
    assessed.some(
      (item) =>
        item.problem != null ||
        item.isLooking ||
        item.invalid > 0 ||
        (item.entity == null && item.contest.record.trim() !== ""),
    ),
  );

  const canCheck = $derived(ops.length > 0 && source != null && !isPending);

  const mapOf = () =>
    ready.map((item) => ({
      contest: item.contest.key,
      cells: item.cells.map(({ row, column }) => ({ row, column })),
    }));

  const session = new Session(
    untrack(() => data.number),
    untrack(() => data.check),
    {
      serialized: () => serialized,
      compiled: () => compiled,
      canCheck: () => canCheck,
      checked: () => {
        checkMap = mapOf();
      },
    },
  );

  checkMap = untrack(mapOf);

  const isLocked = $derived(session.isLocked);

  const nameOf = (item: Assessed) =>
    item.entity?.name ??
    (item.contest.record.trim() || m.ws_results_new_contest());

  const failure = $derived.by(() => {
    const error = session.error;
    if (error == null) {
      return null;
    }
    const target = error.op == null ? null : checkMap[error.op];
    const index = /^\.rows\[(\d+)\]/u.exec(error.path);
    const at =
      target != null && index != null
        ? (target.cells[Number(index[1])] ?? null)
        : null;
    const item = assessed.find(
      (entry) => entry.contest.key === target?.contest,
    );
    const row = item?.contest.rows.find((entry) => entry.key === at?.row);
    return {
      contest: item?.contest.key ?? null,
      at,
      text: error.text,
      label:
        item == null
          ? null
          : at == null || row == null
            ? nameOf(item)
            : m.ws_failure_cell({
                contest: nameOf(item),
                place: row.name,
                column: columnLabel(at.column, item.sheet),
              }),
    };
  });

  const blocker = $derived(
    session.blocker(
      isPending || failure?.contest != null
        ? m.ws_submit_needs_fix()
        : ops.length === 0
          ? m.ws_results_needs_figures()
          : source == null
            ? m.ws_submit_needs_source()
            : null,
      summary,
    ),
  );

  const current = $derived(
    assessed.find((item) => item.contest.key === activeKey) ?? assessed[0],
  );

  const places = $derived(
    new Map((current?.sheet?.places ?? []).map((item) => [item.unit, item])),
  );

  const scheme = $derived.by(() => {
    const tally = new SvelteMap<string, number>();
    for (const item of current?.sheet?.places ?? []) {
      tally.set(item.scheme, (tally.get(item.scheme) ?? 0) + 1);
    }
    let best = "";
    let most = 0;
    for (const [name, total] of tally) {
      if (total > most) {
        best = name;
        most = total;
      }
    }
    return best;
  });

  const marks = $derived(
    current == null
      ? []
      : current.contest.rows.flatMap((row) =>
          current.contest.columns.flatMap((column) => {
            const mark = row.marks[column];
            return mark == null
              ? []
              : [
                  {
                    key: `${row.key}|${column}`,
                    page: mark.page,
                    region: mark.region,
                    label: `${row.name}, ${columnLabel(column, current.sheet)}`,
                  },
                ];
          }),
        ),
  );

  const cellLabel = $derived.by(() => {
    const row = current?.contest.rows.find((entry) => entry.key === cell?.row);
    return row == null || cell == null
      ? null
      : `${row.name}, ${columnLabel(cell.column, current?.sheet ?? null)}`;
  });

  const totalBoxes = $derived.by(() => {
    let total = 0;
    for (const item of assessed) {
      total += item.boxes;
    }
    return total;
  });

  const diffs = $derived(
    (
      [
        ["result_set", m.ws_diff_result_sets],
        ["result_value", m.ws_diff_figures],
        ["value_locator", m.ws_diff_boxes],
        ["outcome", m.ws_diff_outcomes],
      ] as const
    ).flatMap(([table, label]) => {
      const counts = session.check?.checks?.diff?.[table];
      const added = counts?.insert ?? 0;
      const changed = counts?.update ?? 0;
      const dropped = counts?.delete ?? 0;
      return added + changed + dropped === 0
        ? []
        : [{ table, label, added, changed, dropped }];
    }),
  );

  $effect(() => {
    const wanted = [
      ...new Set(contests.map((contest) => contest.record.trim())),
    ].filter((ref) => ref !== "" && !records.has(ref));
    if (wanted.length === 0) {
      return;
    }
    const timer = setTimeout(() => void look(wanted), 400);
    return () => clearTimeout(timer);
  });

  async function look(refs: Array<string>) {
    for (const ref of refs) {
      records.set(ref, null);
    }
    const response = await fetch("/edit/records", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refs }),
    });
    const found = response.ok
      ? ((await response.json()) as Record<string, PageData["records"][string]>)
      : {};
    for (const ref of refs) {
      records.set(ref, found[ref] ?? { state: "missing" });
    }
  }

  $effect(() => {
    for (const item of assessed) {
      const id = item.entity?.id;
      if (id != null && !sheets.has(id)) {
        void load(id);
      }
    }
  });

  async function load(id: string) {
    sheets.set(id, null);
    const response = await fetch(`/edit/contests/${id}`);
    if (response.ok) {
      sheets.set(id, (await response.json()) as Sheet);
    } else {
      sheets.delete(id);
    }
  }

  $effect(() => {
    for (const item of assessed) {
      if (
        item.sheet != null &&
        item.contest.columns.length === 0 &&
        item.contest.rows.length === 0
      ) {
        const sheet = item.sheet;
        untrack(() => {
          item.contest.columns = defaultColumns(sheet);
          item.contest.rows = sheet.places.map((entry) => ({
            key: entry.unit,
            unit: entry.unit,
            name: entry.name,
            values: {},
            marks: {},
          }));
        });
      }
    }
  });

  async function attach(id: string) {
    const response = await fetch(`/uploads/${id}`);
    if (!response.ok) {
      return;
    }
    const meta = (await response.json()) as Upload;
    for (const contest of contests) {
      for (const row of contest.rows) {
        row.marks = {};
      }
    }
    upload = { id: meta.id, filename: meta.filename, pages: meta.pages };
    cited.upload = meta.id;
    page = 1;
  }

  function replaceScan() {
    upload = null;
    cited.upload = null;
  }

  async function focusCell(row: number, column: number) {
    await tick();
    const input = document.getElementById(`rs-${row}-${column}`);
    if (input instanceof HTMLInputElement) {
      input.focus();
      input.select();
    }
  }

  function pickCell(rowKey: string, column: string) {
    const rows = current?.contest.rows ?? [];
    const columns = current?.contest.columns ?? [];
    const r = rows.findIndex((row) => row.key === rowKey);
    const c = columns.indexOf(column);
    if (r !== -1 && c !== -1) {
      void focusCell(r, c);
    }
  }

  function pickMark(key: string) {
    const at = key.lastIndexOf("|");
    pickCell(key.slice(0, at), key.slice(at + 1));
  }

  function choose(row: Row, column: string) {
    cell = { row: row.key, column };
    const mark = row.marks[column];
    if (mark != null && mark.page !== page) {
      page = mark.page;
    }
  }

  function mark(region: Region) {
    const row = current?.contest.rows.find((entry) => entry.key === cell?.row);
    if (row == null || cell == null) {
      return;
    }
    row.marks[cell.column] = { page, region };
    pickCell(row.key, cell.column);
  }

  function unmark() {
    const row = current?.contest.rows.find((entry) => entry.key === cell?.row);
    if (row != null && cell != null) {
      const column = cell.column;
      row.marks = Object.fromEntries(
        Object.entries(row.marks).filter(([key]) => key !== column),
      );
      pickCell(row.key, cell.column);
    }
  }

  function pickFailure() {
    if (failure?.contest == null) {
      return;
    }
    activeKey = failure.contest;
    const at = failure.at;
    if (at != null) {
      pickCell(at.row, at.column);
    }
  }

  function onkey(
    event: KeyboardEvent & { currentTarget: HTMLInputElement },
    r: number,
    c: number,
  ) {
    const input = event.currentTarget;
    const end = input.value.length;
    let to: [number, number] | null = null;
    if (event.key === "Enter") {
      to = [r + (event.shiftKey ? -1 : 1), c];
    } else if (event.key === "ArrowDown") {
      to = [r + 1, c];
    } else if (event.key === "ArrowUp") {
      to = [r - 1, c];
    } else if (
      event.key === "ArrowLeft" &&
      input.selectionStart === 0 &&
      input.selectionEnd === 0
    ) {
      to = [r, c - 1];
    } else if (
      event.key === "ArrowRight" &&
      input.selectionStart === end &&
      input.selectionEnd === end
    ) {
      to = [r, c + 1];
    }
    const rows = current?.contest.rows.length ?? 0;
    const columns = current?.contest.columns.length ?? 0;
    if (
      to == null ||
      to[0] < 0 ||
      to[0] >= rows ||
      to[1] < 0 ||
      to[1] >= columns
    ) {
      return;
    }
    event.preventDefault();
    void focusCell(to[0], to[1]);
  }

  const isText = (value: string) =>
    value.trim() !== "" && Number.isNaN(figure(value));

  const isNumber = (value: string) => {
    const parsed = figure(value);
    return parsed != null && !Number.isNaN(parsed);
  };

  function matchColumn(head: string, item: Assessed) {
    const wanted = fold(head);
    if (wanted === "") {
      return null;
    }
    const labels = item.contest.columns.map((column) => ({
      column,
      label: fold(columnLabel(column, item.sheet)),
      code: column.startsWith("measure:") ? fold(column.slice(8)) : null,
    }));
    const exact = labels.filter(
      (entry) => entry.label === wanted || entry.code === wanted,
    );
    if (exact.length === 1) {
      return exact[0]?.column ?? null;
    }
    const near = labels.filter(
      (entry) =>
        ` ${entry.label} `.includes(` ${wanted} `) ||
        ` ${wanted} `.includes(` ${entry.label} `),
    );
    return near.length === 1 ? (near[0]?.column ?? null) : null;
  }

  function matchRow(label: string, item: Assessed) {
    const wanted = fold(label);
    if (wanted === "") {
      return null;
    }
    const hits = new SvelteSet<number>();
    for (const [index, row] of item.contest.rows.entries()) {
      const code =
        typeof row.unit === "string"
          ? (places.get(row.unit)?.code ?? "")
          : row.unit.code;
      if (fold(row.name) === wanted || fold(code) === wanted) {
        hits.add(index);
      }
    }
    if (hits.size === 1) {
      return [...hits][0] ?? null;
    }
    if (hits.size > 1) {
      return null;
    }
    for (const [index, row] of item.contest.rows.entries()) {
      const name = fold(row.name);
      if (name.startsWith(`${wanted} `) || wanted.startsWith(`${name} `)) {
        hits.add(index);
      }
    }
    return hits.size === 1 ? ([...hits][0] ?? null) : null;
  }

  function paste(event: ClipboardEvent, r: number, c: number) {
    const text = event.clipboardData?.getData("text/plain") ?? "";
    const item = current;
    if (item == null || !/[\t\n]/u.test(text.trim())) {
      return;
    }
    event.preventDefault();
    const contest = item.contest;
    const undo = $state.snapshot(contest);
    const table = splitTable(text);
    const top = table[0] ?? [];
    const header =
      table.length > 1 &&
      top.slice(1).some(isText) &&
      top.slice(1).every((value) => value.trim() === "" || isText(value)) &&
      (table[1] ?? []).some(isNumber);
    const lines = header ? table.slice(1) : table;
    const labeled =
      lines.filter((line) => isText(line[0] ?? "")).length * 2 >= lines.length;
    const skip = labeled ? 1 : 0;
    let widest = 0;
    for (const line of lines) {
      widest = Math.max(widest, line.length - skip);
    }
    const positional = Array.from(
      { length: widest },
      (_, index) => contest.columns[c + index] ?? null,
    );
    const mapped = header
      ? top.slice(skip).map((head) => matchColumn(head, item))
      : [];
    const targets = mapped.some((column) => column != null)
      ? mapped
      : positional;
    let filled = 0;
    let added = 0;
    let skipped = 0;
    for (const [n, line] of lines.entries()) {
      let index: number | null = labeled
        ? matchRow(line[0] ?? "", item)
        : r + n;
      if (labeled && index == null) {
        const code = (line[0] ?? "").trim();
        const unit = { scheme, code };
        const key = placeKey(unit);
        index = contest.rows.findIndex((row) => row.key === key);
        if (index === -1) {
          contest.rows.push({ key, unit, name: code, values: {}, marks: {} });
          index = contest.rows.length - 1;
          added += 1;
        }
      }
      const row = index == null ? undefined : contest.rows[index];
      if (row == null) {
        skipped += 1;
        continue;
      }
      for (const [i, value] of line.slice(skip).entries()) {
        const column = targets[i];
        if (column != null && value.trim() !== "") {
          row.values[column] = value.trim();
        }
      }
      filled += 1;
    }
    pasted = {
      contest: contest.key,
      rows: filled,
      added,
      skipped,
      undo: { ...undo, key: contest.key },
    };
  }

  function undoPaste() {
    const last = pasted;
    if (last == null) {
      return;
    }
    const at = contests.findIndex((contest) => contest.key === last.contest);
    if (at !== -1) {
      contests[at] = last.undo;
    }
    pasted = null;
  }

  function addContest() {
    const contest = blankContest();
    contests.push(contest);
    activeKey = contest.key;
    cell = null;
    void tick().then(() => document.getElementById("rs-contest")?.focus());
  }

  function removeContest(key: number) {
    contests = contests.filter((contest) => contest.key !== key);
    if (contests.length === 0) {
      contests = [blankContest()];
    }
    activeKey = contests[0]?.key ?? 0;
    cell = null;
  }

  function addPlace(event: SubmitEvent) {
    event.preventDefault();
    const contest = current?.contest;
    const unit = { scheme: place.scheme.trim(), code: place.code.trim() };
    const key = placeKey(unit);
    if (
      contest == null ||
      unit.scheme === "" ||
      unit.code === "" ||
      contest.rows.some((row) => row.key === key)
    ) {
      return;
    }
    contest.rows.push({ key, unit, name: unit.code, values: {}, marks: {} });
    place.code = "";
  }

  function toggleColumn(key: string, on: boolean) {
    const contest = current?.contest;
    if (contest == null) {
      return;
    }
    const wanted = new SvelteSet(contest.columns);
    if (on) {
      wanted.add(key);
    } else {
      wanted.delete(key);
    }
    contest.columns = [
      ...(current?.sheet?.options ?? []).map((option) => option.id),
      ...unitMeasures.map((measure) => measureKey(measure)),
    ].filter((column) => wanted.has(column));
  }

  function retarget(contest: Contest) {
    const typed = contest.rows.some((row) =>
      Object.values(row.values).some((value) => value.trim() !== ""),
    );
    if (!typed) {
      contest.columns = [];
      contest.rows = [];
      cell = null;
    }
  }

  function resize(event: PointerEvent) {
    if (!dragging || body == null) {
      return;
    }
    const box = body.getBoundingClientRect();
    split = Math.min(
      72,
      Math.max(24, ((event.clientX - box.left) / box.width) * 100),
    );
  }
</script>

<Shell
  {session}
  number={data.number}
  heading={templateTitle.results()}
  bind:summary
  {blocker}
  failure={failure == null
    ? null
    : {
        label: failure.label,
        text: failure.text,
        pick: failure.contest == null ? null : pickFailure,
      }}
>
  {#snippet outline()}
    <ul class="grid px-2 pbe-2">
      {#each assessed as item (item.contest.key)}
        {const problems = $derived(
          (item.problem == null ? 0 : 1) +
            item.invalid +
            item.mismatched.size +
            item.printedOff.size +
            (failure?.contest === item.contest.key ? 1 : 0),
        )}
        <li>
          <button
            type="button"
            class={cn(
              "grid gap-0.5 rounded-sm px-2 py-1.5 text-start inline-full hover:bg-muted",
              { "bg-muted": item.contest.key === activeKey },
            )}
            aria-current={item.contest.key === activeKey}
            onclick={() => {
              activeKey = item.contest.key;
              cell = null;
            }}
          >
            <span class="flex items-baseline justify-between gap-2">
              <span class="truncate font-semibold min-inline-0"
                >{nameOf(item)}</span
              >
              {#if problems > 0}
                <span class="font-figures font-semibold text-destructive"
                  >{problems}</span
                >
              {/if}
            </span>
            {#if item.stage != null && item.entity != null}
              <span class="text-xs text-muted-foreground"
                >{stageLabel[item.stage]()}</span
              >
            {/if}
            {#if item.cells.length > 0}
              <span class="font-figures text-xs"
                >{m.ws_results_places({ count: item.places })}, {m.ws_results_figures(
                  { count: item.cells.length },
                )}{#if item.boxes > 0}, {m.ws_results_boxes({
                    count: item.boxes,
                  })}{/if}</span
              >
            {/if}
          </button>
        </li>
      {/each}
    </ul>
    {#if !isLocked}
      <Button
        variant="ghost"
        size="sm"
        class="mx-2 text-muted-foreground"
        onclick={addContest}><PlusIcon />{m.ws_results_add_contest()}</Button
      >
    {/if}
  {/snippet}

  {#snippet editor()}
    {#if current != null}
      {const contest = $derived(current.contest)}
      {const chosen = $derived(current.stage)}
      <div
        class="grid grid-rows-[auto_minmax(0,1fr)] lg:block-full lg:min-block-0"
      >
        <section
          class="grid gap-1.5 border-be px-4 py-2.5"
          aria-label={m.ws_results_contest()}
        >
          <div class="flex flex-wrap items-center gap-2">
            <Input
              id="rs-contest"
              bind:value={contest.record}
              disabled={isLocked}
              placeholder={m.ws_results_contest_placeholder()}
              aria-label={m.ws_results_contest()}
              aria-invalid={current.problem != null}
              autocomplete="off"
              spellcheck={false}
              maxlength={2048}
              class="flex-1 basis-60 block-8 min-inline-0"
              oninput={() => retarget(contest)}
            />
            <Select.Root
              type="single"
              value={contest.stage}
              disabled={isLocked}
              onValueChange={(value: string) => {
                contest.stage = value;
              }}
            >
              <Select.Trigger
                class="text-sm block-8"
                aria-label={m.edit_results_stage()}
                >{chosen == null
                  ? m.edit_results_stage()
                  : stageLabel[chosen]()}</Select.Trigger
              >
              <Select.Content>
                {#each stages as stage (stage)}
                  <Select.Item value={stage}>{stageLabel[stage]()}</Select.Item>
                {/each}
              </Select.Content>
            </Select.Root>
            {#if current.sheet != null}
              {const sheet = $derived(current.sheet)}
              <DropdownMenu.Root>
                <DropdownMenu.Trigger disabled={isLocked}>
                  {#snippet child({ props })}
                    <Button {...props} variant="outline" size="sm"
                      ><Columns3Icon />{m.ws_results_columns()}</Button
                    >
                  {/snippet}
                </DropdownMenu.Trigger>
                <DropdownMenu.Content align="end" class="max-inline-80">
                  {#each sheet.options as option (option.id)}
                    <DropdownMenu.CheckboxItem
                      checked={contest.columns.includes(option.id)}
                      onCheckedChange={(on: boolean) =>
                        toggleColumn(option.id, on)}
                      ><span class="truncate">{option.label}</span
                      ></DropdownMenu.CheckboxItem
                    >
                  {/each}
                  {#if sheet.options.length > 0}
                    <DropdownMenu.Separator />
                  {/if}
                  {#each unitMeasures as measure (measure)}
                    {const key = $derived(measureKey(measure))}
                    <DropdownMenu.CheckboxItem
                      checked={contest.columns.includes(key)}
                      onCheckedChange={(on: boolean) => toggleColumn(key, on)}
                      >{columnLabel(key, sheet)}</DropdownMenu.CheckboxItem
                    >
                  {/each}
                </DropdownMenu.Content>
              </DropdownMenu.Root>
            {/if}
            <Button
              variant="ghost"
              size="icon"
              class="block-8 inline-8 max-xl:hidden"
              aria-label={showScan ? m.ws_scan_hide() : m.ws_scan_show()}
              aria-pressed={showScan}
              onclick={() => (showScan = !showScan)}
              >{#if showScan}<PanelLeftCloseIcon />{:else}<PanelLeftOpenIcon
                />{/if}</Button
            >
            {#if contests.length > 1 && !isLocked}
              <Button
                variant="ghost"
                size="icon"
                class="text-muted-foreground block-8 inline-8"
                aria-label={m.ws_results_remove_contest()}
                onclick={() => removeContest(contest.key)}><XIcon /></Button
              >
            {/if}
          </div>
          <p
            class={cn("text-xs text-pretty", {
              "font-semibold text-destructive": current.problem != null,
              "text-muted-foreground": current.problem == null,
            })}
          >
            {#if current.problem != null}
              {current.problem}
            {:else if current.isLooking}
              {m.ws_record_looking()}
            {:else if current.entity != null}
              {const entity = $derived(current.entity)}
              {const held = $derived(current.sheet?.set ?? null)}
              {const heldStage = $derived(
                stages.find((item) => item === held?.stage),
              )}
              <a
                class="font-semibold text-foreground underline decoration-border underline-offset-4"
                href={resolve("/id/[id]", { id: entity.id })}
                target="_blank"
                rel="noopener">{entity.name}</a
              >
              {#if heldStage != null}
                {m.ws_results_prefilled({
                  stage: stageLabel[heldStage]().toLowerCase(),
                })}
              {/if}
            {:else}
              {m.ws_results_start()}
            {/if}
          </p>
        </section>

        <div
          bind:this={body}
          class={cn("grid min-block-0", {
            "xl:grid-cols-[var(--scan)_0.5rem_minmax(0,1fr)]": showScan,
            "select-none": dragging,
          })}
          style:--scan="{split}%"
        >
          <section
            class={cn(
              "grid border-be bg-background block-[55dvh] min-block-0 xl:border-be-0 xl:block-auto",
              { "xl:hidden": !showScan },
            )}
            aria-label={m.ws_scan_title()}
          >
            {#if upload != null}
              <Scan
                {upload}
                bind:page
                {marks}
                selected={cell == null ? null : `${cell.row}|${cell.column}`}
                armed={cellLabel}
                locked={isLocked}
                onmark={mark}
                onpick={pickMark}
                onunmark={unmark}
              />
            {:else}
              <div class="grid content-start gap-3 p-4">
                <div class="grid gap-1">
                  <h2 class="font-semibold">{m.ws_scan_title()}</h2>
                  <p class="text-xs text-pretty text-muted-foreground">
                    {m.ws_scan_upload_hint()}
                  </p>
                </div>
                {#if !isLocked}
                  <UploadEvidence
                    bind:value={
                      () => null,
                      (next: { id: string } | null) => {
                        if (next != null) {
                          void attach(next.id);
                        }
                      }
                    }
                  />
                {/if}
              </div>
            {/if}
          </section>
          {#if showScan}
            <button
              type="button"
              class="group grid cursor-col-resize touch-none place-items-center max-xl:hidden"
              aria-label={m.ws_scan_resize()}
              onpointerdown={(event) => {
                event.currentTarget.setPointerCapture(event.pointerId);
                dragging = true;
              }}
              onpointermove={resize}
              onpointerup={() => (dragging = false)}
              onpointercancel={() => (dragging = false)}
              onkeydown={(event) => {
                if (event.key === "ArrowLeft") {
                  split = Math.max(24, split - 2);
                } else if (event.key === "ArrowRight") {
                  split = Math.min(72, split + 2);
                }
              }}
            >
              <span
                class={cn(
                  "bg-border block-full inline-px group-hover:bg-foreground group-focus-visible:bg-foreground",
                  { "bg-foreground": dragging },
                )}
              ></span>
            </button>
          {/if}

          <section
            class="grid grid-rows-[minmax(0,1fr)_auto] min-block-0"
            aria-label={templateTitle.results()}
          >
            {#if contest.columns.length === 0}
              <p class="p-4 text-pretty text-muted-foreground">
                {current.sheet?.options.length === 0
                  ? m.ws_results_no_options()
                  : m.ws_results_start()}
              </p>
            {:else}
              {const sheet = $derived(current.sheet)}
              {const firstMeasure = $derived(
                contest.columns.find((column) => column.startsWith("measure:")),
              )}
              <div class="overflow-auto min-block-0 max-xl:max-block-[70dvh]">
                <table
                  class="border-separate border-spacing-0 font-figures text-sm inline-full"
                >
                  <thead class="sticky inset-bs-0 z-20 bg-background">
                    <tr class="text-xs text-muted-foreground">
                      <th
                        class="sticky inset-s-0 z-10 border-be bg-background px-3 py-1.5 text-start font-normal min-inline-40"
                        >{m.edit_results_place()}</th
                      >
                      {#each contest.columns as column (column)}
                        <th
                          class={cn(
                            "border-be px-2 py-1.5 text-end align-bottom font-normal text-balance max-inline-36 min-inline-22",
                            {
                              "border-s": column === firstMeasure,
                              "text-foreground": cell?.column === column,
                            },
                          )}
                          scope="col">{columnLabel(column, sheet)}</th
                        >
                      {/each}
                    </tr>
                  </thead>
                  <tbody>
                    {#each contest.rows as row, r (row.key)}
                      {const off = $derived(current.mismatched.has(row.key))}
                      {const isNew = $derived(typeof row.unit !== "string")}
                      {const info = $derived(
                        typeof row.unit === "string"
                          ? places.get(row.unit)
                          : undefined,
                      )}
                      <tr class="align-baseline">
                        <th
                          scope="row"
                          class={cn(
                            "sticky inset-s-0 z-10 border-be bg-background px-3 py-1 text-start font-normal",
                            {
                              "shadow-[inset_2px_0_0_var(--destructive)]": off,
                              "bg-muted": cell?.row === row.key,
                            },
                          )}
                        >
                          <span
                            class="flex items-baseline justify-between gap-2"
                          >
                            <span class="truncate font-sans max-inline-44"
                              >{row.name}</span
                            >
                            {#if isNew && !isLocked}
                              <button
                                type="button"
                                class="shrink-0 text-muted-foreground hover:text-foreground"
                                aria-label={m.edit_results_remove_place()}
                                onclick={() =>
                                  (contest.rows = contest.rows.filter(
                                    (entry) => entry.key !== row.key,
                                  ))}
                                ><XIcon class="block-3.5 inline-3.5" /></button
                              >
                            {/if}
                          </span>
                          <span
                            class={cn("block text-xs", {
                              "font-semibold text-destructive": off,
                              "text-muted-foreground": !off,
                            })}
                            >{#if off}{m.edit_results_row_sum({
                                sum: count(current.sums.get(row.key) ?? 0),
                              })}{:else if isNew}{m.ws_results_new_place()}{:else if info != null}{info.scheme}
                              {info.code}{/if}</span
                          >
                        </th>
                        {#each contest.columns as column, c (column)}
                          {const raw = $derived(row.values[column] ?? "")}
                          {const parsed = $derived(figure(raw))}
                          {const linked = $derived(row.marks[column] != null)}
                          {const isCell = $derived(
                            cell?.row === row.key && cell.column === column,
                          )}
                          {const isFailed = $derived(
                            failure?.contest === contest.key &&
                              failure.at?.row === row.key &&
                              failure.at.column === column,
                          )}
                          <td
                            class={cn("border-be p-0.5", {
                              "border-s": column === firstMeasure,
                            })}
                          >
                            <input
                              id="rs-{r}-{c}"
                              class={cn(
                                "rounded-xs border border-transparent bg-transparent px-1.5 text-end tabular-nums shadow-[inset_0_-1px_0_var(--border)] block-8 inline-full hover:border-input focus-visible:border-ring focus-visible:outline-none disabled:opacity-60",
                                {
                                  "shadow-[inset_0_-3px_0_var(--marker)]":
                                    linked,
                                  "bg-marker/45": linked && isCell,
                                },
                                {
                                  "border-destructive": Number.isNaN(parsed),
                                  "ring-[1.5px] ring-destructive": isFailed,
                                },
                              )}
                              inputmode="decimal"
                              autocomplete="off"
                              value={raw}
                              disabled={isLocked}
                              aria-label="{row.name}, {columnLabel(
                                column,
                                sheet,
                              )}"
                              aria-invalid={Number.isNaN(parsed) || isFailed}
                              onfocus={() => choose(row, column)}
                              oninput={(
                                event: Event & {
                                  currentTarget: HTMLInputElement;
                                },
                              ) =>
                                (row.values[column] =
                                  event.currentTarget.value)}
                              onkeydown={(
                                event: KeyboardEvent & {
                                  currentTarget: HTMLInputElement;
                                },
                              ) => onkey(event, r, c)}
                              onpaste={(event: ClipboardEvent) =>
                                paste(event, r, c)}
                            />
                          </td>
                        {/each}
                      </tr>
                    {/each}
                  </tbody>
                  <tfoot class="sticky inset-be-0 z-20 bg-background">
                    <tr class="font-semibold">
                      <th
                        scope="row"
                        class="sticky inset-s-0 z-10 border-bs-2 border-foreground bg-background px-3 py-1.5 text-start"
                        >{m.edit_results_total()}</th
                      >
                      {#each contest.columns as column (column)}
                        <td
                          class={cn(
                            "border-bs-2 border-foreground px-2 py-1.5 text-end tabular-nums",
                            { "border-s": column === firstMeasure },
                          )}>{count(current.totals.get(column) ?? 0)}</td
                        >
                      {/each}
                    </tr>
                    <tr>
                      <th
                        scope="row"
                        class="sticky inset-s-0 z-10 bg-background px-3 py-1 text-start text-xs font-normal text-muted-foreground"
                        >{m.ws_results_printed()}</th
                      >
                      {#each contest.columns as column (column)}
                        {const isOff = $derived(current.printedOff.has(column))}
                        <td
                          class={cn("p-0.5", {
                            "border-s": column === firstMeasure,
                          })}
                        >
                          <input
                            class={cn(
                              "rounded-xs border border-dashed border-input bg-transparent px-1.5 text-end text-xs tabular-nums block-7 inline-full focus-visible:border-ring focus-visible:outline-none",
                              { "border-solid border-destructive": isOff },
                            )}
                            inputmode="decimal"
                            autocomplete="off"
                            disabled={isLocked}
                            value={contest.printed[column] ?? ""}
                            aria-label="{m.ws_results_printed()}, {columnLabel(
                              column,
                              sheet,
                            )}"
                            aria-invalid={isOff}
                            title={isOff
                              ? m.ws_results_printed_off({
                                  sum: count(current.totals.get(column) ?? 0),
                                })
                              : undefined}
                            oninput={(
                              event: Event & {
                                currentTarget: HTMLInputElement;
                              },
                            ) =>
                              (contest.printed[column] =
                                event.currentTarget.value)}
                          />
                        </td>
                      {/each}
                    </tr>
                    <tr>
                      <th
                        scope="row"
                        class="sticky inset-s-0 z-10 bg-background px-3 py-1 text-start text-xs font-normal text-muted-foreground"
                        >{m.ws_results_outcome()}</th
                      >
                      {#each contest.columns as column (column)}
                        {const kind = $derived(
                          outcomeKinds.find(
                            (item) => item === contest.outcomes[column],
                          ),
                        )}
                        <td
                          class={cn("p-0.5", {
                            "border-s": column === firstMeasure,
                          })}
                        >
                          {#if !column.startsWith("measure:")}
                            <Select.Root
                              type="single"
                              value={contest.outcomes[column] ?? ""}
                              disabled={isLocked}
                              onValueChange={(value: string) => {
                                contest.outcomes[column] = value;
                              }}
                            >
                              <Select.Trigger
                                class={cn(
                                  "justify-end gap-1 px-1.5 text-xs block-7 inline-full",
                                  { "text-muted-foreground": kind == null },
                                )}
                                aria-label="{m.ws_results_outcome()}, {columnLabel(
                                  column,
                                  sheet,
                                )}"
                                >{kind == null
                                  ? m.ws_results_outcome_none()
                                  : outcomeLabel(kind)}</Select.Trigger
                              >
                              <Select.Content>
                                <Select.Item value=""
                                  >{m.ws_results_outcome_none()}</Select.Item
                                >
                                {#each outcomeKinds as item (item)}
                                  <Select.Item value={item}
                                    >{outcomeLabel(item)}</Select.Item
                                  >
                                {/each}
                              </Select.Content>
                            </Select.Root>
                          {/if}
                        </td>
                      {/each}
                    </tr>
                  </tfoot>
                </table>
              </div>
              <div class="grid gap-2 border-bs px-4 py-2.5">
                {#if pasted?.contest === contest.key}
                  <p
                    class="flex flex-wrap items-baseline gap-x-2 text-xs text-pretty"
                    role="status"
                  >
                    <span class="font-semibold"
                      >{m.ws_results_pasted({ count: pasted.rows })}</span
                    >
                    {#if pasted.added > 0}
                      <span
                        >{m.ws_results_pasted_new({
                          count: pasted.added,
                        })}</span
                      >
                    {/if}
                    {#if pasted.skipped > 0}
                      <span
                        >{m.ws_results_pasted_skipped({
                          count: pasted.skipped,
                        })}</span
                      >
                    {/if}
                    <button
                      type="button"
                      class="underline underline-offset-2"
                      onclick={undoPaste}>{m.ws_results_undo()}</button
                    >
                  </p>
                {/if}
                {#if current.invalid > 0}
                  <p
                    class="text-xs font-semibold text-destructive"
                    role="alert"
                  >
                    {m.edit_results_invalid()}
                  </p>
                {/if}
                {#if current.mismatched.size > 0}
                  <p class="text-xs font-semibold" role="status">
                    {m.edit_results_mismatch({
                      count: current.mismatched.size,
                    })}
                  </p>
                {/if}
                {#if !isLocked}
                  <form
                    class="flex flex-wrap items-center gap-2"
                    onsubmit={addPlace}
                  >
                    <Input
                      bind:value={place.scheme}
                      placeholder={scheme}
                      class="text-sm block-8 inline-32"
                      aria-label={m.edit_results_scheme()}
                    />
                    <Input
                      bind:value={place.code}
                      class="text-sm block-8 inline-40"
                      aria-label={m.edit_results_code()}
                    />
                    <Button type="submit" variant="outline" size="sm"
                      ><PlusIcon />{m.edit_results_add_place()}</Button
                    >
                  </form>
                {/if}
              </div>
            {/if}
          </section>
        </div>
      </div>
    {/if}
  {/snippet}

  {#snippet diff()}
    {#if diffs.length === 0}
      <p class="text-pretty text-muted-foreground">{m.ws_diff_empty()}</p>
    {:else}
      {#if session.isStale}
        <p class="pbe-2 text-xs font-semibold">{m.ws_checks_stale()}</p>
      {/if}
      <ul class="grid border-s-2 border-foreground">
        {#each diffs as row (row.table)}
          <li
            class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-2 border-be py-1.5 ps-3"
          >
            <span>{row.label()}</span>
            <span class="font-figures font-semibold tabular-nums"
              >{#if row.added > 0}+{count(row.added)}{/if}{#if row.changed > 0}
                ~{count(row.changed)}{/if}{#if row.dropped > 0}
                −{count(row.dropped)}{/if}</span
            >
          </li>
        {/each}
      </ul>
    {/if}
  {/snippet}

  {#snippet evidence()}
    {const kind = $derived(cited.kind)}
    <fieldset class="contrib-settle grid gap-2" disabled={isLocked}>
      <legend class="pbe-2 font-semibold">{m.edit_evidence()}</legend>
      {#if upload != null}
        <p class="flex items-baseline justify-between gap-3">
          <span class="truncate" title={upload.filename}
            >{m.ws_evidence_scan({ filename: upload.filename })}</span
          >
          <button
            type="button"
            class="shrink-0 text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
            onclick={replaceScan}>{m.upload_replace()}</button
          >
        </p>
        {#if totalBoxes > 0}
          <p class="text-xs text-pretty text-muted-foreground">
            {m.ws_scan_replace_note({ count: totalBoxes })}
          </p>
        {/if}
      {:else}
        <p class="text-xs text-pretty text-muted-foreground">
          {m.ws_evidence_scan_or_link()}
        </p>
        <Input
          type="url"
          inputmode="url"
          bind:value={cited.url}
          placeholder="https://"
          aria-label={m.edit_url()}
          maxlength={2048}
          class="block-8"
        />
      {/if}
      <Input
        bind:value={cited.title}
        placeholder={m.edit_url_title_placeholder()}
        aria-label={m.edit_url_title()}
        maxlength={500}
        class="block-8"
      />
      <Input
        bind:value={cited.publisher}
        placeholder={m.edit_url_publisher_placeholder()}
        aria-label={m.edit_url_publisher()}
        maxlength={200}
        class="block-8"
      />
      <Select.Root
        type="single"
        value={kind}
        onValueChange={(value: string) => {
          const found = sourceKinds.find((item) => item === value);
          if (found != null) {
            cited.kind = found;
          }
        }}
      >
        <Select.Trigger
          class="text-sm block-8 inline-full"
          aria-label={m.edit_url_kind()}
          >{sourceKindLabel[kind]()}</Select.Trigger
        >
        <Select.Content>
          {#each sourceKinds as item (item)}
            <Select.Item value={item}>{sourceKindLabel[item]()}</Select.Item>
          {/each}
        </Select.Content>
      </Select.Root>
      {#if upload == null}
        <p class="text-xs text-pretty text-muted-foreground">
          {m.edit_url_note()}
        </p>
      {/if}
    </fieldset>
  {/snippet}

  {#snippet commands(act: (action: () => void) => void)}
    <Command.Item disabled={isLocked} onSelect={() => act(addContest)}
      >{m.ws_results_add_contest()}</Command.Item
    >
    <Command.Item onSelect={() => act(() => (showScan = !showScan))}
      >{showScan ? m.ws_scan_hide() : m.ws_scan_show()}</Command.Item
    >
    {#if upload != null}
      <Command.Item disabled={isLocked} onSelect={() => act(replaceScan)}
        >{m.upload_replace()}</Command.Item
      >
    {/if}
    {#if pasted != null}
      <Command.Item onSelect={() => act(undoPaste)}
        >{m.ws_results_undo()}</Command.Item
      >
    {/if}
  {/snippet}
</Shell>
