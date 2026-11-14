<script lang="ts">
  import { resolve } from "$app/paths";
  import { ArrowUpRightIcon } from "@lucide/svelte";
  import type { Site } from "#lib/server/site.js";
  import Cite from "#lib/components/cite.svelte";
  import Language from "#lib/components/language.svelte";
  import Monogram from "#lib/components/monogram.svelte";
  import SystemStatus from "#lib/components/system-status.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import { moment, REPOSITORY } from "#lib/site.js";
  import { cn } from "#lib/utils.js";

  const { site }: { site: Site } = $props();

  const groups = [
    {
      title: m.footer_archive(),
      links: [
        { href: resolve("/coverage"), label: m.coverage_nav() },
        { href: resolve("/compare"), label: m.versus_action() },
        { href: resolve("/live/methodology"), label: m.footer_live_model() },
      ],
    },
    {
      title: m.footer_data(),
      links: [
        { href: resolve("/sources"), label: m.sources_title() },
        {
          href: resolve("/[slug=project]", { slug: "license" }),
          label: m.license_title(),
        },
        { href: "/api/v1/openapi.json", label: m.footer_api() },
        { href: resolve("/agents"), label: m.agents_title() },
      ],
    },
    {
      title: m.footer_project(),
      links: [
        {
          href: resolve("/[slug=project]", { slug: "about" }),
          label: m.about_title(),
        },
        {
          href: resolve("/[slug=project]", { slug: "architecture" }),
          label: m.architecture_title(),
        },
        {
          href: resolve("/[slug=project]", { slug: "contribute" }),
          label: m.contribute_title(),
        },
        { href: REPOSITORY, label: m.footer_github() },
      ],
    },
  ];

  let status = $state<"idle" | "sending" | "done" | "failed">("idle");

  async function subscribe(
    event: SubmitEvent & { currentTarget: HTMLFormElement },
  ) {
    event.preventDefault();
    const form = event.currentTarget;
    status = "sending";
    try {
      await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        mode: "no-cors",
      });
      form.reset();
      status = "done";
    } catch {
      status = "failed";
    }
  }
</script>

<footer class="border-bs-[3px] border-foreground">
  <div
    class="mx-auto grid gap-x-12 gap-y-8 px-4 pbs-8 pbe-6 max-inline-7xl lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,19rem)] max-sm:px-3"
  >
    <div class="grid content-start gap-2">
      <a
        href={resolve("/")}
        class="flex items-center gap-2.5 justify-self-start focus-visible:outline-2 focus-visible:outline-ring"
      >
        <Monogram size={22} />
        <span class="text-base font-[780] tracking-[-0.015em]"
          >{m.wordmark()}</span
        >
      </a>
      <p
        class="font-serif text-[0.9375rem]/relaxed text-pretty text-muted-foreground max-inline-[36ch]"
      >
        {m.footer_tagline()}
      </p>
      <p class="text-sm text-pretty text-muted-foreground">
        <span
          class="-my-1.25 me-1.5 inline-block -rotate-3 rounded-[2px/7px] border-y-[5px] border-transparent bg-marker bg-clip-padding px-1 align-[0.0625rem] text-[0.625rem]/[0.9rem] font-[780] tracking-[0.02em] text-[#15171a]"
          >{m.beta_label()}</span
        >{m.footer_beta()}
      </p>
      <p class="text-sm text-muted-foreground">
        <a
          href={resolve("/[slug=project]", { slug: "license" })}
          class="underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
          >{m.footer_licenses()}</a
        >
      </p>
    </div>

    <nav
      aria-label={m.footer_project()}
      class="grid grid-cols-3 gap-x-10 gap-y-6 max-sm:grid-cols-2 max-sm:gap-x-6"
    >
      {#each groups as group (group.title)}
        <div class="grid content-start gap-2">
          <h2 class="text-xs font-semibold text-muted-foreground">
            {group.title}
          </h2>
          <ul class="grid gap-1.5 text-sm">
            {#each group.links as link (link.href)}
              <li>
                <a
                  href={link.href}
                  rel={link.href === REPOSITORY ? "external" : undefined}
                  class="inline-flex items-center gap-0.5 underline decoration-transparent underline-offset-4 hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
                  >{link.label}{#if link.href === REPOSITORY}<ArrowUpRightIcon
                      class="text-muted-foreground block-3.5 inline-3.5"
                      aria-hidden="true"
                    />{/if}</a
                >
              </li>
            {/each}
          </ul>
        </div>
      {/each}
    </nav>

    <form
      method="post"
      action="https://buttondown.com/api/emails/embed-subscribe/firatciftci"
      target="_blank"
      class="grid content-start gap-2"
      onsubmit={subscribe}
    >
      <label
        for="footer-email"
        class="text-xs font-semibold text-muted-foreground"
        >{m.footer_updates_title()}</label
      >
      <p id="footer-email-hint" class="text-sm text-muted-foreground">
        {m.footer_updates_hint()}
      </p>
      <input type="hidden" name="embed" value="1" />
      <input type="hidden" name="tag" value="electionsarchive" />
      <div class="flex gap-1.5">
        <Input
          id="footer-email"
          name="email"
          type="email"
          required
          autocomplete="email"
          placeholder={m.footer_email_placeholder()}
          aria-describedby="footer-email-hint footer-email-status"
          class="flex-1 bg-card block-8"
        />
        <Button type="submit" disabled={status === "sending"}
          >{status === "sending"
            ? m.footer_subscribing()
            : m.footer_subscribe()}</Button
        >
      </div>
      <p
        id="footer-email-status"
        role="status"
        class={cn("text-sm min-block-5", {
          "text-destructive": status === "failed",
        })}
      >
        {#if status === "done"}
          {m.footer_subscribed()}
        {:else if status === "failed"}
          {m.footer_subscribe_failed()}
        {/if}
      </p>
    </form>
  </div>

  <div class="border-bs">
    <div
      class="mx-auto flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5 font-figures text-[0.8125rem] text-muted-foreground tabular-nums max-inline-7xl max-sm:px-3"
    >
      <span class="flex items-center gap-2.5">
        <SystemStatus live={site.live} published={site.published} />
        {#if site.published != null}
          <a
            href={resolve("/recent")}
            class="underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
            >{m.footer_data_as_of({ time: moment(site.published) })}</a
          >
        {/if}
      </span>
      {#if site.release != null}
        <span class="flex items-center gap-2">
          {#if site.release.doi == null}
            <a
              href="{REPOSITORY}/releases/tag/data-{site.release.calver}"
              rel="external"
              class="underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
              aria-label={m.footer_release_download({
                version: site.release.calver,
              })}>{m.footer_release({ version: site.release.calver })}</a
            >
          {:else}
            {m.footer_release({ version: site.release.calver })}
            <a
              href="https://doi.org/{site.release.doi}"
              rel="external"
              class="underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
              aria-label={m.footer_release_download({
                version: site.release.calver,
              })}>{site.release.doi}</a
            >
          {/if}
        </span>
      {/if}
      <span class="flex items-center gap-2">
        <a
          href="{resolve('/changelog')}{site.build.version === 'dev'
            ? ''
            : `#v${site.build.version}`}"
          class="underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
          aria-label={m.footer_version_label({ version: site.build.version })}
          >{m.footer_version({ version: site.build.version })}</a
        >
        {#if site.build.commit != null}
          <a
            href="{REPOSITORY}/commit/{site.build.commit}"
            rel="external"
            class="underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
            aria-label={m.footer_commit({ sha: site.build.commit.slice(0, 7) })}
            >{site.build.commit.slice(0, 7)}</a
          >
        {/if}
        {#if site.build.isDirty}
          <span title={m.footer_dirty_label()}>{m.footer_dirty()}</span>
        {/if}
      </span>
      <Cite published={site.published} release={site.release} />
    </div>
  </div>

  <div class="border-bs">
    <div
      class="mx-auto flex flex-wrap items-center gap-x-5 gap-y-2 p-4 text-sm text-muted-foreground max-inline-7xl max-sm:px-3"
    >
      <a
        href="https://www.firatciftci.com"
        rel="external"
        class="group flex items-center gap-2.5 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
      >
        <span
          aria-hidden="true"
          class="aspect-[277.9/301.2] shrink-0 rotate-20 bg-current mask-[url(/shahmaran.svg)] mask-contain mask-no-repeat opacity-30 transition-[rotate,opacity] duration-700 inline-7 group-hover:rotate-[8deg] group-hover:opacity-70 group-focus-visible:rotate-[8deg] group-focus-visible:opacity-70 motion-reduce:transition-none"
        ></span>
        <span
          >{m.footer_credit()}
          <span
            class="font-semibold text-foreground underline decoration-border underline-offset-4 group-hover:decoration-current"
            >Firat Ciftci</span
          ></span
        >
      </a>
      <span class="ms-auto max-sm:ms-0">{m.footer_rights()}</span>
      <Language />
      <a
        href={resolve("/[slug=project]", { slug: "privacy" })}
        class="underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
        >{m.privacy_title()}</a
      >
      <a
        href={resolve("/[slug=project]", { slug: "terms" })}
        class="underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
        >{m.terms_title()}</a
      >
    </div>
  </div>
</footer>
