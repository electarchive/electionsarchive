<script lang="ts">
  import { copy } from "./copy.js";
  import { count, parties, percent, turnout } from "./election.js";

  const { fonts }: { fonts: string } = $props();

  const names = [
    ["Ekrem İmamoğlu", "tr"],
    ["Kemal Kılıçdaroğlu", "tr"],
    ["BÜNDNIS 90/DIE GRÜNEN", "de"],
    ["Les Écologistes", "fr"],
    ["Ευρωπαϊκό Κοινοβούλιο", "el"],
    ["Европейски парламент", "bg"],
    ["مجلس مبعوثان", "ota"],
  ] as const;
</script>

<div class="sp">
  <div class="sp-block">
    <span class="sp-label">{copy.specimen_display}</span>
    <p class="sp-display">{copy.title}</p>
  </div>
  <div class="sp-block">
    <span class="sp-label">{copy.specimen_interface}</span>
    <p class="sp-ui">{copy.note_districts}</p>
  </div>
  <div class="sp-block">
    <span class="sp-label">{copy.specimen_numbers}</span>
    <div class="sp-numbers">
      {#each parties.slice(0, 4) as party (party.code)}
        <span>{count.format(party.second)}</span>
        <span>{percent(party.secondShare, 2)}</span>
      {/each}
      <span>{count.format(630)}</span>
      <span>{percent(turnout, 2)}</span>
    </div>
  </div>
  <div class="sp-block">
    <span class="sp-label">{copy.specimen_scripts}</span>
    <ul class="sp-names">
      {#each names as [name, lang] (name)}
        <li {lang} dir={lang === "ota" ? "rtl" : undefined}>{name}</li>
      {/each}
    </ul>
    <p class="sp-coverage">
      {copy.specimen_coverage.replace("{fonts}", fonts)}
    </p>
  </div>
  <div class="sp-buttons">
    <button type="button" class="btn">{copy.button_compare}</button>
    <button type="button" class="btn">{copy.button_download}</button>
    <button type="button" class="btn btn-primary">{copy.button_cite}</button>
  </div>
</div>

<style>
  .sp {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 22px 28px;
    color: var(--ink);
  }

  .sp-block {
    display: grid;
    gap: 8px;
    align-content: start;
  }

  .sp-label {
    color: var(--muted);
    font: 12px/1.3 var(--font-ui);
  }

  .sp-display {
    font-family: var(--font-display);
    font-size: 34px;
    font-weight: var(--display-weight, 500);
    line-height: 1.04;
    letter-spacing: var(--display-tracking, -0.015em);
    text-wrap: balance;
  }

  .sp-ui {
    max-inline-size: 46ch;
    font: 14px/1.5 var(--font-ui);
  }

  .sp-numbers {
    display: grid;
    grid-template-columns: auto auto;
    justify-content: start;
    gap: 2px 18px;
    font-family: var(--font-num);
    font-size: 14px;
    font-variant-numeric: tabular-nums;
  }

  .sp-numbers span:nth-child(odd) {
    text-align: end;
  }

  .sp-names {
    display: grid;
    gap: 3px;
    font: 15px/1.35 var(--font-ui);
  }

  .sp-names li[lang="ota"] {
    justify-self: start;
    font-family: "Noto Naskh Arabic", var(--font-ui);
  }

  .sp-coverage {
    color: var(--muted);
    font: 12px/1.45 var(--font-ui);
  }

  .sp-buttons {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    grid-column: 1 / -1;
  }
</style>
