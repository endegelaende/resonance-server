<script lang="ts">
  let {
    label = "",
    status = "",
    color = "gray",
  }: { label?: string; status?: string; color?: string } = $props();

  const colorMap: Record<string, string> = {
    green: "bg-success/20 text-success",
    red: "bg-error/20 text-error",
    yellow: "bg-warning/20 text-warning",
    blue: "bg-accent/20 text-accent",
    gray: "bg-overlay-0/20 text-overlay-1",
  };

  const classes = $derived(colorMap[color] ?? colorMap.gray);
</script>

<span
  class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium {classes}"
>
  <span class="h-1.5 w-1.5 rounded-full bg-current"></span>
  {label || status}
</span>
