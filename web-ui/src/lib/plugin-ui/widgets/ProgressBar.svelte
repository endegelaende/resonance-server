<script lang="ts">
  let {
    value = 0,
    label = null,
    color = null,
  }: { value?: number; label?: string | null; color?: string | null } =
    $props();

  const colorMap: Record<string, string> = {
    green: "bg-success",
    red: "bg-error",
    yellow: "bg-warning",
    blue: "bg-accent",
    gray: "bg-overlay-0",
  };

  const barColor = $derived(
    color ? (colorMap[color] ?? "bg-accent") : "bg-accent",
  );
  const clamped = $derived(Math.max(0, Math.min(100, value)));
</script>

<div>
  {#if label}
    <div class="flex items-center justify-between mb-1">
      <span class="text-sm text-overlay-1">{label}</span>
      <span class="text-sm text-text font-medium">{clamped}%</span>
    </div>
  {/if}
  <div class="h-2 rounded-full bg-surface-1 overflow-hidden">
    <div
      class="h-full rounded-full transition-all duration-300 {barColor}"
      style="width: {clamped}%"
    ></div>
  </div>
</div>
