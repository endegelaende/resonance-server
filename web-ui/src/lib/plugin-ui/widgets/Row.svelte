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

  const justifyClass = $derived(
    justify ? (justifyMap[justify] ?? "") : "",
  );
  const alignClass = $derived(align ? (alignMap[align] ?? "") : "");
</script>

<div class="flex flex-wrap gap-{gap} {justifyClass} {alignClass}">
  {#if children}
    {@render children()}
  {/if}
</div>
