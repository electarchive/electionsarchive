<script lang="ts">
  const { text }: { text: string } = $props();

  const words = $derived(text.split(" "));
</script>

{#each words as word, index (index)}<span class="whitespace-nowrap">{word}</span
  >{index < words.length - 1 ? " " : ""}{/each}
