<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    gap = "4",
    justify = null,
    align = null,
    children,
  }: {
    gap?: string;
    justify?: string | null;
    align?: string | null;
    children?: Snippet;
  } = $props();

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

  const justifyMap: Record<string, string> = {
    start: "justify-start",
    center: "justify-center",
    end: "justify-end",
    between: "justify-between",
  };

  const alignMap: Record<string, string> = {
    start: "items-start",
    center: "items-center",
    end: "items-end",
    stretch: "items-stretch",
  };

  const gapClass = $derived(gapMap[gap] ?? "gap-4");
  const justifyClass = $derived(
    justify ? (justifyMap[justify] ?? "") : "",
  );
  const alignClass = $derived(align ? (alignMap[align] ?? "") : "");
</script>

<div class="flex flex-wrap {gapClass} {justifyClass} {alignClass}">
  {#if children}
    {@render children()}
  {/if}
</div>
