<script lang="ts">
  let {
    message = "",
    severity = "info",
    title = null,
  }: { message?: string; severity?: string; title?: string | null } =
    $props();

  const severityMap: Record<string, { bg: string; border: string; text: string }> = {
    info: {
      bg: "bg-accent/10",
      border: "border-accent/30",
      text: "text-accent",
    },
    success: {
      bg: "bg-success/10",
      border: "border-success/30",
      text: "text-success",
    },
    warning: {
      bg: "bg-warning/10",
      border: "border-warning/30",
      text: "text-warning",
    },
    error: {
      bg: "bg-error/10",
      border: "border-error/30",
      text: "text-error",
    },
  };

  const styles = $derived(severityMap[severity] ?? severityMap.info);
</script>

<div class="rounded-lg border p-4 {styles.bg} {styles.border}">
  {#if title}
    <h4 class="font-medium mb-1 {styles.text}">{title}</h4>
  {/if}
  <p class="text-sm text-text">{message}</p>
</div>
