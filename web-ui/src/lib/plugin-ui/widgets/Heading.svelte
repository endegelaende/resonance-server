<script lang="ts">
  let { text = "", level = 2 }: { text?: string; level?: number } = $props();
</script>

{#if level === 1}
  <h1 class="text-2xl font-bold text-text">{text}</h1>
{:else if level === 2}
  <h2 class="text-xl font-semibold text-text">{text}</h2>
{:else if level === 3}
  <h3
    class="text-sm font-semibold text-subtext-1 uppercase tracking-wider"
  >
    {text}
  </h3>
{:else}
  <h4 class="text-sm font-medium text-subtext-0">{text}</h4>
{/if}
