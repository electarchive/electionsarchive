<script lang="ts">
  import { CopyIcon } from "@lucide/svelte";
  import { toast } from "svelte-sonner";
  import { Button } from "#lib/components/ui/button/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const mcp = $derived(`${data.origin}/mcp`);
  const rest = $derived(`${data.origin}/api/v1`);
  const skill = $derived(`${data.origin}/agents/skill.md`);
  const config = $derived(
    JSON.stringify(
      {
        mcpServers: {
          electionsarchive: {
            type: "http",
            url: mcp,
            headers: { "x-api-key": "ea_…" },
          },
        },
      },
      null,
      2,
    ),
  );
  const endpoints = $derived([
    { label: m.agents_mcp(), url: mcp, link: null },
    {
      label: m.agents_rest(),
      url: rest,
      link: { href: "/api/v1/openapi.json", label: m.agents_openapi() },
    },
    {
      label: m.agents_skill(),
      url: skill,
      link: { href: "/agents/skill.md", label: m.agents_open() },
    },
  ]);
  const count = new Intl.NumberFormat("en");
  const rules = $derived([
    { title: m.agents_rule_evidence_title(), body: m.agents_rule_evidence() },
    {
      title: m.agents_rule_limits_title(),
      body: m.agents_rule_limits({
        minute: count.format(data.writes.minute),
        day: count.format(data.writes.day),
      }),
    },
    { title: m.agents_rule_blocked_title(), body: m.agents_rule_blocked() },
    { title: m.agents_rule_credit_title(), body: m.agents_rule_credit() },
  ]);

  function copy(text: string) {
    void navigator.clipboard
      .writeText(text)
      .then(() => toast.success(m.agents_copied()));
  }
</script>

<svelte:head>
  <title>{m.agents_title()} | {m.wordmark()}</title>
  <meta name="description" content={m.agents_lede()} />
</svelte:head>

<main class="mx-auto grid gap-12 px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-2">
    <h1 class="title">{m.agents_title()}</h1>
    <p
      class="text-[0.9375rem] text-pretty text-muted-foreground max-inline-[60ch]"
    >
      {m.agents_lede()}
    </p>
  </header>

  <section
    class="grid border-bs-[3px] border-foreground"
    aria-labelledby="endpoints"
  >
    <h2 id="endpoints" class="sr-only">{m.agents_endpoints()}</h2>
    <dl class="grid">
      {#each endpoints as endpoint (endpoint.url)}
        <div
          class="grid items-center gap-x-4 gap-y-1 border-be py-3 sm:grid-cols-[8rem_1fr_auto]"
        >
          <dt class="text-sm text-muted-foreground">{endpoint.label}</dt>
          <dd class="font-figures text-lg break-all">{endpoint.url}</dd>
          <dd>
            {#if endpoint.link == null}
              <Button
                variant="outline"
                size="sm"
                onclick={() => copy(endpoint.url)}
              >
                <CopyIcon />{m.agents_copy()}
              </Button>
            {:else}
              <Button
                variant="outline"
                size="sm"
                href={endpoint.link.href}
                target="_blank">{endpoint.link.label}</Button
              >
            {/if}
          </dd>
        </div>
      {/each}
    </dl>
  </section>

  <section class="grid gap-4" aria-labelledby="setup">
    <h2 id="setup" class="heading">{m.agents_setup()}</h2>
    <ol class="grid border-bs">
      <li class="grid grid-cols-[2rem_1fr] gap-x-3 border-be py-4">
        <span class="font-figures text-lg font-semibold">1</span>
        <div class="grid gap-1.5">
          <h3 class="font-semibold">{m.agents_step_key_title()}</h3>
          <p class="text-sm text-pretty text-muted-foreground">
            {m.agents_step_key()}
          </p>
          <div>
            <Button size="sm" href="/settings/keys"
              >{m.agents_step_key_link()}</Button
            >
          </div>
        </div>
      </li>
      <li class="grid grid-cols-[2rem_1fr] gap-x-3 border-be py-4">
        <span class="font-figures text-lg font-semibold">2</span>
        <div class="grid gap-1.5 min-inline-0">
          <h3 class="font-semibold">{m.agents_step_connect_title()}</h3>
          <p class="text-sm text-pretty text-muted-foreground">
            {m.agents_step_connect()}
          </p>
          <div class="grid gap-1.5 pbs-1">
            <div class="flex items-center justify-between gap-3">
              <span class="text-xs text-muted-foreground"
                >{m.agents_config()}</span
              >
              <Button variant="ghost" size="sm" onclick={() => copy(config)}>
                <CopyIcon />{m.agents_copy()}
              </Button>
            </div>
            <pre
              class="field overflow-x-auto px-3 py-2 font-figures text-[0.8125rem] leading-relaxed">{config}</pre>
          </div>
        </div>
      </li>
      <li class="grid grid-cols-[2rem_1fr] gap-x-3 border-be py-4">
        <span class="font-figures text-lg font-semibold">3</span>
        <div class="grid gap-1.5">
          <h3 class="font-semibold">{m.agents_step_skill_title()}</h3>
          <p class="text-sm text-pretty text-muted-foreground">
            {m.agents_step_skill()}
          </p>
        </div>
      </li>
      <li class="grid grid-cols-[2rem_1fr] gap-x-3 border-be py-4">
        <span class="font-figures text-lg font-semibold">4</span>
        <div class="grid gap-1.5">
          <h3 class="font-semibold">{m.agents_step_review_title()}</h3>
          <p class="text-sm text-pretty text-muted-foreground">
            {m.agents_step_review()}
          </p>
        </div>
      </li>
    </ol>
  </section>

  <section class="grid gap-4" aria-labelledby="tools">
    <div class="grid gap-1">
      <h2 id="tools" class="heading">{m.agents_tools()}</h2>
      <p class="text-sm text-muted-foreground">{m.agents_tools_note()}</p>
    </div>
    <table class="border-bs text-sm inline-full">
      <thead class="max-sm:sr-only">
        <tr class="border-be text-muted-foreground">
          <th class="py-2 pe-4 text-start font-normal">{m.agents_col_tool()}</th
          >
          <th class="py-2 pe-4 text-start font-normal">{m.agents_col_does()}</th
          >
          <th class="py-2 text-end font-normal"
            ><span class="sr-only">{m.agents_key()}</span></th
          >
        </tr>
      </thead>
      <tbody>
        {#each data.tools as tool (tool.name)}
          <tr
            class="border-be align-baseline max-sm:grid max-sm:grid-cols-[1fr_auto] max-sm:gap-x-3 max-sm:gap-y-0.5 max-sm:py-2.5"
          >
            <td class="py-2 pe-4 whitespace-nowrap max-sm:p-0">
              <code class="font-figures font-semibold">{tool.name}</code>
            </td>
            <td
              class="py-2 pe-4 text-pretty text-muted-foreground max-sm:col-span-2 max-sm:row-start-2 max-sm:p-0"
              >{tool.description}</td
            >
            <td
              class="py-2 text-end text-xs font-semibold whitespace-nowrap max-sm:col-start-2 max-sm:row-start-1 max-sm:p-0"
            >
              {#if tool.isKeyed}{m.agents_key()}{/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </section>

  <section class="grid gap-4" aria-labelledby="rules">
    <h2 id="rules" class="heading">{m.agents_rules()}</h2>
    <dl class="grid border-bs">
      {#each rules as rule (rule.title)}
        <div
          class="grid gap-x-4 gap-y-1 border-be py-3 sm:grid-cols-[8rem_1fr]"
        >
          <dt class="font-semibold">{rule.title}</dt>
          <dd class="text-sm text-pretty text-muted-foreground">{rule.body}</dd>
        </div>
      {/each}
    </dl>
  </section>
</main>
