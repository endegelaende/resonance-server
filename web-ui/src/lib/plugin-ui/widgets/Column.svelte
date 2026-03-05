<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    gap = "4",
    children,
  }: { gap?: string; children?: Snippet } = $props();

  const gapMap: Record<string, string> = {
    "0": "gap-0",
    "1": "gap-1",
    "2": "gap-2",
    "3": "gap-3",
    "4": "gap-4",
    "5": "gap-5",
    "6": "gap-6",
    "8": "gap-8",
    "10": "gap-10",
    "12": "gap-12",
  };

  const gapClass = $derived(gapMap[gap] ?? "gap-4");
</script>

<div class="flex flex-col {gapClass}">
  {#if children}
    {@render children()}
  {/if}
</div>
