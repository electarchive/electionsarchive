<script lang="ts">
  import type { SubmitFunction } from "$app/forms";
  import Tabs from "#routes/review/tabs.svelte";
  import { enhance } from "$app/forms";
  import { resolve } from "$app/paths";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { Label } from "#lib/components/ui/label/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import { stateLabel } from "#lib/contrib.js";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  const kinds = [
    "cap",
    "probation",
    "no_upload",
    "no_talk",
    "no_artifacts",
  ] as const;
  type Kind = (typeof kinds)[number];
  const kindLabel: Record<Kind, () => string> = {
    cap: m.limit_cap,
    probation: m.limit_probation,
    no_upload: m.limit_no_upload,
    no_talk: m.limit_no_talk,
    no_artifacts: m.limit_no_artifacts,
  };
  const jobLabel: Record<string, () => string> = {
    queued: m.account_job_queued,
    running: m.account_job_running,
    done: m.account_job_done,
    failed: m.account_job_failed,
  };

  let kind = $state<Kind>("probation");
  let pending = $state<string | null>(null);

  const megabytes = (value: number) =>
    value >= 1024 ** 3
      ? `${count(value / 1024 ** 3)} GB`
      : `${count(Math.round(value / 1024 ** 2))} MB`;

  const submit =
    (action: string): SubmitFunction =>
    () => {
      pending = action;
      return async ({ update }) => {
        await update();
        pending = null;
      };
    };
</script>

<svelte:head>
  <title
    >{data.allowed ? data.account.name : m.accounts_title()} | {m.review_title()}</title
  >
  <meta name="robots" content="noindex" />
</svelte:head>

{#snippet outcome(action: string)}
  {#if form?.action === action}
    {#if "message" in form}
      <p class="text-sm text-destructive" role="alert">{form.message}</p>
    {:else if form.queued != null}
      <p class="text-sm" role="status">
        {m.account_revert_queued({ count: form.queued })}
      </p>
    {:else}
      <p class="text-sm" role="status">{m.account_saved()}</p>
    {/if}
  {/if}
{/snippet}

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-5xl max-sm:px-3">
  <Tabs current="accounts" />
  {#if !data.allowed}
    <h1 class="mbs-4 title">{m.review_title()}</h1>
    <p class="mbs-4 text-sm text-muted-foreground">{m.review_forbidden()}</p>
  {:else}
    {const standing = $derived(data.standing)}
    <p class="mbs-4 text-sm">
      <a class="underline underline-offset-2" href={resolve("/review/accounts")}
        >{m.account_back()}</a
      >
    </p>
    <header class="mbs-3 grid gap-1">
      <h1 class="title text-balance">{data.account.name}</h1>
      <p
        class="flex flex-wrap gap-x-4 gap-y-0.5 text-sm text-pretty text-muted-foreground"
      >
        {#if data.account.handle != null}
          <a
            class="font-semibold text-foreground underline-offset-2 hover:underline"
            href="/contributors/{data.account.handle}">@{data.account.handle}</a
          >
        {/if}
        <span class="break-all">{data.account.email}</span>
        <span
          >{m.account_joined({
            date: date(data.account.created_at.slice(0, 10)),
          })}</span
        >
      </p>
    </header>

    <dl
      class="mbs-6 grid grid-cols-4 gap-px border-y bg-border max-sm:grid-cols-2"
    >
      <div class="grid gap-0.5 bg-background py-3 ps-3 first:ps-0">
        <dt class="text-xs text-muted-foreground">{m.account_level_label()}</dt>
        <dd class="font-figures text-2xl/none font-semibold">
          {standing.level}
        </dd>
      </div>
      <div class="grid gap-0.5 bg-background py-3 ps-3">
        <dt class="text-xs text-muted-foreground">{m.account_today()}</dt>
        <dd class="font-figures text-2xl/none font-semibold">
          {count(standing.submitted)}<span
            class="text-sm font-normal text-muted-foreground"
          >
            / {standing.proposals == null
              ? m.account_no_cap()
              : count(standing.proposals)}</span
          >
        </dd>
      </div>
      <div class="grid gap-0.5 bg-background py-3 ps-3 max-sm:ps-0">
        <dt class="text-xs text-muted-foreground">{m.account_uploads()}</dt>
        <dd class="font-figures text-2xl/none font-semibold">
          {megabytes(standing.uploaded)}<span
            class="text-sm font-normal text-muted-foreground"
          >
            / {megabytes(standing.uploads)}</span
          >
        </dd>
      </div>
      <div class="grid gap-0.5 bg-background py-3 ps-3">
        <dt class="text-xs text-muted-foreground">{m.account_status()}</dt>
        <dd class="text-sm font-semibold">
          {#if standing.banned}
            <span class="text-destructive">{m.account_banned()}</span>
          {:else if standing.limits.length > 0}
            {m.account_limited()}
          {:else}
            {m.account_in_good_standing()}
          {/if}
        </dd>
      </div>
    </dl>

    <div class="mbs-10 grid gap-x-12 gap-y-10 lg:grid-cols-2">
      <section class="grid content-start gap-3" aria-labelledby="limits">
        <h2 id="limits" class="heading">{m.account_limits()}</h2>
        {#if data.limits.length === 0}
          <p class="border-bs pbs-3 text-sm text-muted-foreground">
            {m.account_limits_none()}
          </p>
        {:else}
          <ul class="grid border-bs text-sm">
            {#each data.limits as limit (limit.id)}
              <li
                class="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 border-be py-2.5"
              >
                <span class="grid gap-0.5">
                  <span class:text-muted-foreground={!limit.active}>
                    <span class="font-semibold">{kindLabel[limit.kind]()}</span
                    >{#if limit.kind === "cap" && limit.value?.proposals != null}:
                      {m.account_limit_cap_value({
                        count: limit.value.proposals,
                      })}{/if}
                  </span>
                  <span class="text-pretty">{limit.reason}</span>
                  <span class="text-xs text-muted-foreground">
                    {m.account_limit_set_by({
                      name: limit.set_by,
                      date: date(limit.created_at.slice(0, 10)),
                    })}{#if limit.lifted_at != null}. {m.account_limit_lifted({
                        name: limit.lifted_by ?? "",
                        date: date(limit.lifted_at.slice(0, 10)),
                      })}{:else if limit.expires_at != null}. {m.account_limit_expires(
                        { date: date(limit.expires_at.slice(0, 10)) },
                      )}{/if}
                  </span>
                </span>
                {#if limit.active}
                  <form
                    method="POST"
                    action="?/lift"
                    use:enhance={submit("lift")}
                  >
                    <input type="hidden" name="limit" value={limit.id} />
                    <Button
                      type="submit"
                      size="sm"
                      variant="outline"
                      disabled={pending != null}
                      >{m.account_limit_lift()}</Button
                    >
                  </form>
                {/if}
              </li>
            {/each}
          </ul>
          {@render outcome("lift")}
        {/if}
        <form
          method="POST"
          action="?/limit"
          class="grid gap-3 pbs-2"
          use:enhance={submit("limit")}
        >
          <div class="grid gap-1.5">
            <Label id="limit-kind-label">{m.account_limit_kind()}</Label>
            <Select.Root
              type="single"
              name="kind"
              value={kind}
              onValueChange={(chosen: string) => {
                kind = kinds.find((item) => item === chosen) ?? "probation";
              }}
            >
              <Select.Trigger
                class="inline-full"
                aria-labelledby="limit-kind-label"
                >{kindLabel[kind]()}</Select.Trigger
              >
              <Select.Content>
                {#each kinds as item (item)}
                  <Select.Item value={item}>{kindLabel[item]()}</Select.Item>
                {/each}
              </Select.Content>
            </Select.Root>
          </div>
          {#if kind === "cap"}
            <div class="grid gap-1.5">
              <Label for="limit-proposals">{m.account_limit_proposals()}</Label>
              <Input
                id="limit-proposals"
                name="proposals"
                type="number"
                min={0}
                max={10000}
                required
                inputmode="numeric"
              />
            </div>
          {/if}
          <div class="grid gap-1.5">
            <Label for="limit-reason">{m.account_reason()}</Label>
            <Textarea
              id="limit-reason"
              name="reason"
              rows={2}
              required
              maxlength={2000}
            />
          </div>
          <div class="grid gap-1.5">
            <Label for="limit-days">{m.account_days()}</Label>
            <Input
              id="limit-days"
              name="days"
              type="number"
              min={1}
              max={3650}
              inputmode="numeric"
              aria-describedby="limit-days-hint"
            />
            <p id="limit-days-hint" class="text-xs text-muted-foreground">
              {m.account_days_hint()}
            </p>
          </div>
          {@render outcome("limit")}
          <Button
            type="submit"
            class="justify-self-start"
            disabled={pending != null}>{m.account_limit_add()}</Button
          >
        </form>
      </section>

      <div class="grid content-start gap-10">
        <section class="grid content-start gap-3" aria-labelledby="ban">
          <h2 id="ban" class="heading">{m.account_ban_heading()}</h2>
          {#if standing.banned}
            <p class="border-bs pbs-3 text-sm text-pretty">
              {data.account.ban_expires == null
                ? m.account_banned_reason({
                    reason: data.account.ban_reason ?? "",
                  })
                : m.account_banned_until({
                    date: date(data.account.ban_expires.slice(0, 10)),
                    reason: data.account.ban_reason ?? "",
                  })}
            </p>
            <form method="POST" action="?/unban" use:enhance={submit("unban")}>
              <Button type="submit" variant="outline" disabled={pending != null}
                >{m.account_unban()}</Button
              >
            </form>
            {@render outcome("unban")}
          {:else}
            <p
              class="border-bs pbs-3 text-sm text-pretty text-muted-foreground"
            >
              {m.account_ban_lede()}
            </p>
            <form
              method="POST"
              action="?/ban"
              class="grid gap-3"
              use:enhance={submit("ban")}
            >
              <div class="grid gap-1.5">
                <Label for="ban-reason">{m.account_reason()}</Label>
                <Textarea
                  id="ban-reason"
                  name="reason"
                  rows={2}
                  required
                  maxlength={2000}
                />
              </div>
              <div class="grid gap-1.5">
                <Label for="ban-days">{m.account_days()}</Label>
                <Input
                  id="ban-days"
                  name="days"
                  type="number"
                  min={1}
                  max={3650}
                  inputmode="numeric"
                  aria-describedby="ban-days-hint"
                />
                <p id="ban-days-hint" class="text-xs text-muted-foreground">
                  {m.account_days_hint()}
                </p>
              </div>
              {@render outcome("ban")}
              <Button
                type="submit"
                variant="destructive"
                class="justify-self-start"
                disabled={pending != null}>{m.account_ban_submit()}</Button
              >
            </form>
          {/if}
        </section>

        {#if data.admin}
          <section class="grid content-start gap-3" aria-labelledby="revert">
            <h2 id="revert" class="heading">{m.account_revert_heading()}</h2>
            <p
              class="border-bs pbs-3 text-sm text-pretty text-muted-foreground"
            >
              {m.account_revert_lede()}
            </p>
            <form
              method="POST"
              action="?/revert"
              class="grid gap-3"
              use:enhance={submit("revert")}
            >
              <div class="grid gap-1.5">
                <Label for="revert-since">{m.account_revert_since()}</Label>
                <Input
                  id="revert-since"
                  name="since"
                  type="date"
                  required
                  class="max-inline-48"
                />
              </div>
              <div class="grid gap-1.5">
                <Label for="revert-reason">{m.account_revert_reason()}</Label>
                <Textarea
                  id="revert-reason"
                  name="reason"
                  rows={2}
                  required
                  maxlength={2000}
                />
              </div>
              {@render outcome("revert")}
              <Button
                type="submit"
                variant="destructive"
                class="justify-self-start"
                disabled={pending != null}>{m.account_revert_submit()}</Button
              >
            </form>
            {#if data.reverts.length > 0}
              <h3 class="mbs-2 text-sm font-semibold">
                {m.account_reverts()}
              </h3>
              <ul class="grid border-bs text-sm">
                {#each data.reverts as job (`${job.number}-${job.created_at}`)}
                  <li class="grid gap-0.5 border-be py-2">
                    <span class="flex justify-between gap-3">
                      <a
                        class="truncate underline-offset-4 hover:underline"
                        href={resolve("/proposals/[number]", {
                          number: String(job.number),
                        })}
                        ><span class="font-figures text-muted-foreground"
                          >#{job.number}</span
                        >
                        {job.title}</a
                      >
                      <span
                        class="shrink-0 text-xs"
                        class:text-destructive={job.status === "failed"}
                        >{jobLabel[job.status]?.() ?? job.status}</span
                      >
                    </span>
                    {#if job.error != null}
                      <span
                        class="text-xs text-pretty wrap-break-word text-muted-foreground"
                        >{job.error}</span
                      >
                    {/if}
                  </li>
                {/each}
              </ul>
            {/if}
          </section>
        {/if}
      </div>
    </div>

    <section class="mbs-10" aria-labelledby="proposals">
      <h2 id="proposals" class="heading">{m.contributor_proposals()}</h2>
      {#if data.proposals.length === 0}
        <p class="mbs-2 border-bs pbs-3 text-sm text-muted-foreground">
          {m.contributor_no_proposals()}
        </p>
      {:else}
        <ul class="mbs-2 grid border-bs">
          {#each data.proposals as item (item.number)}
            <li class="border-be">
              <a
                class="group grid grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-x-3 py-2.5 text-sm"
                href={resolve("/proposals/[number]", {
                  number: String(item.number),
                })}
              >
                <span class="font-figures text-muted-foreground"
                  >#{item.number}</span
                >
                <span class="truncate underline-offset-4 group-hover:underline"
                  >{item.title}</span
                >
                <span class="text-xs text-muted-foreground"
                  >{stateLabel[item.state]()}</span
                >
              </a>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  {/if}
</main>
