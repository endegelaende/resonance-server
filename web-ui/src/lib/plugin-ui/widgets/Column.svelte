<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    gap = "4",
    children,
  }: { gap?: string; children?: Snippet } = $props();
</script>

<div class="flex flex-col gap-{gap}">
  {#if children}
    {@render children()}
  {/if}
</div>
