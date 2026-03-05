<script lang="ts">
  let { items = [] }: { items?: Array<{ key: string; value: string; color?: string }> } =
    $props();

  const colorMap: Record<string, string> = {
    green: "text-success",
    red: "text-error",
    yellow: "text-warning",
    blue: "text-accent",
    gray: "text-overlay-1",
  };
</script>

<div class="space-y-2">
  {#each items as item}
    <div class="flex items-center justify-between py-1">
      <span class="text-sm text-overlay-1">{item.key}</span>
      <span
        class="text-sm font-medium {item.color
          ? (colorMap[item.color] ?? 'text-text')
          : 'text-text'}"
      >
        {item.value}
      </span>
    </div>
  {/each}
</div>
