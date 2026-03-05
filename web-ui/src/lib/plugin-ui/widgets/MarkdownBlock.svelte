<script lang="ts">
  let { content = "" }: { content?: string } = $props();

  // Simple markdown-to-text conversion (no @html — security by construction).
  // Strips markdown syntax for safe plain-text rendering.
  // A full markdown renderer can be added in Phase 2 with a sanitization library.
  function stripMarkdown(md: string): string[] {
    return md.split("\n").filter((line) => line.trim().length > 0);
  }

  const lines = $derived(stripMarkdown(content));
</script>

<div class="space-y-2 text-sm text-text">
  {#each lines as line}
    <p>{line}</p>
  {/each}
</div>
