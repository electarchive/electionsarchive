import { untrack } from "svelte";
import { findingsOf } from "#lib/contrib.js";
import { m } from "#lib/paraglide/messages.js";
import type { PageData } from "./$types";

type Check = PageData["check"];

type Source = {
  serialized: () => string;
  compiled: () => string;
  canCheck: () => boolean;
  checked?: () => void;
};

const read = async <T>(response: Response) => response.json() as Promise<T>;

async function reason(response: Response) {
  try {
    const body = await read<{ message?: string }>(response);
    return body.message ?? response.statusText;
  } catch {
    return response.statusText;
  }
}

export class Session {
  readonly #number: number;
  readonly #source: Source;
  #queue = Promise.resolve(true);

  check = $state<Check>(null);
  checkedFor = $state<string | null>(null);
  checkError = $state<string | null>(null);
  isChecking = $state(false);
  saved = $state<string | null>(null);
  saveError = $state<string | null>(null);
  isSaving = $state(false);
  isSubmitting = $state(false);
  submitted = $state<number | null>(null);
  submitError = $state<string | null>(null);
  tab = $state<"diff" | "checks" | "evidence">("checks");
  palette = $state(false);

  readonly isLocked = $derived(this.submitted != null);

  readonly isStale = $derived(
    this.check != null && this.#compiled() !== this.checkedFor,
  );

  readonly error = $derived.by(() => {
    if (this.check?.status !== "failed" || this.check.error == null) {
      return null;
    }
    const text = this.check.error.replace(/^\w+Error: /u, "");
    const at = /^ops\[(\d+)\]((?:\.\w+|\[\d+\])*): /u.exec(text);
    return {
      op: at == null ? null : Number(at[1]),
      path: at?.[2] ?? "",
      text: at == null ? text : text.slice(at[0].length),
    };
  });

  readonly findings = $derived(findingsOf(this.check?.checks));

  readonly findingCount = $derived.by(() => {
    let total = 0;
    for (const finding of this.findings) {
      total += finding.value;
    }
    return total;
  });

  readonly saveStatus = $derived(
    this.saveError == null
      ? this.isSaving
        ? m.ws_saving()
        : this.#serialized() === this.saved
          ? m.ws_saved()
          : m.ws_unsaved()
      : m.ws_save_failed({ reason: this.saveError }),
  );

  constructor(number: number, check: Check, source: Source) {
    this.#number = number;
    this.#source = source;
    this.check = check;
    this.saved = untrack(source.serialized);
    this.checkedFor = check == null ? null : untrack(source.compiled);

    $effect(() => {
      if (this.#serialized() === this.saved || this.isLocked) {
        return;
      }
      const timer = setTimeout(() => void this.save(), 800);
      return () => clearTimeout(timer);
    });

    $effect(() => {
      if (
        !this.#source.canCheck() ||
        this.isLocked ||
        this.isChecking ||
        this.#compiled() === this.checkedFor
      ) {
        return;
      }
      const timer = setTimeout(() => void this.runChecks(), 3000);
      return () => clearTimeout(timer);
    });
  }

  #serialized() {
    return this.#source.serialized();
  }

  #compiled() {
    return this.#source.compiled();
  }

  async #post(action: "check" | "submit") {
    const response = await fetch(`/edit/${this.#number}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ do: action }),
    });
    if (!response.ok) {
      return { check: null, error: await reason(response) };
    }
    const body = await read<{ check: Check }>(response);
    return { check: body.check, error: null };
  }

  blocker(own: string | null, summary: string) {
    return (
      own ??
      (summary.trim() === ""
        ? m.ws_submit_needs_summary()
        : this.check?.status !== "done" || this.isStale
          ? m.ws_submit_needs_check()
          : null)
    );
  }

  async save() {
    const previous = this.#queue;
    this.#queue = (async () => {
      await previous;
      const body = this.#serialized();
      if (body === this.saved) {
        return true;
      }
      this.isSaving = true;
      try {
        const response = await fetch(`/edit/${this.#number}`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body,
        });
        if (!response.ok) {
          this.saveError = await reason(response);
          return false;
        }
        this.saved = body;
        this.saveError = null;
        return true;
      } catch (error_) {
        this.saveError =
          error_ instanceof Error ? error_.message : String(error_);
        return false;
      } finally {
        this.isSaving = false;
      }
    })();
    return this.#queue;
  }

  async runChecks() {
    if (this.isChecking || this.isLocked || !this.#source.canCheck()) {
      return;
    }
    this.tab = "checks";
    this.isChecking = true;
    this.checkError = null;
    const snapshot = this.#compiled();
    try {
      if (!(await this.save())) {
        return;
      }
      const result = await this.#post("check");
      if (result.error != null) {
        this.checkError = result.error;
        return;
      }
      this.check = result.check;
      this.checkedFor = snapshot;
      this.#source.checked?.();
    } finally {
      this.isChecking = false;
    }
  }

  async submit(blocker: string | null) {
    if (blocker != null || this.isSubmitting || this.isLocked) {
      return;
    }
    this.isSubmitting = true;
    this.submitError = null;
    try {
      if (!(await this.save())) {
        return;
      }
      const result = await this.#post("submit");
      if (result.error != null) {
        this.submitError = result.error;
        return;
      }
      this.submitError =
        result.check?.status === "failed"
          ? (result.check.error?.replace(/^\w+Error: /u, "") ?? null)
          : null;
      this.submitted = this.#number;
    } finally {
      this.isSubmitting = false;
    }
  }
}
