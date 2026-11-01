<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";

  const { text, onInput }: { text?: string; onInput: (text: string) => void } =
    $props();
</script>

<textarea
  value={text ?? ""}
  oninput={(event) => onInput(event.currentTarget.value)}
  placeholder={m.board_note_placeholder()}
  aria-label={m.board_kind_note()}
  maxlength="2000"
  class="resize-none bg-transparent text-[0.875em] leading-snug outline-none block-full inline-full placeholder:text-muted-foreground"
></textarea>
