<script lang="ts">
  let {
    content = "",
    color = null,
    size = null,
  }: { content?: string; color?: string | null; size?: string | null } =
    $props();

  const colorMap: Record<string, string> = {
    green: "text-success",
    red: "text-error",
    yellow: "text-warning",
    blue: "text-accent",
    gray: "text-overlay-1",
  };

  const sizeMap: Record<string, string> = {
    sm: "text-sm",
    md: "text-base",
    lg: "text-lg",
  };

  const colorClass = $derived(
    color ? (colorMap[color] ?? "text-text") : "text-text",
  );
  const sizeClass = $derived(
    size ? (sizeMap[size] ?? "text-base") : "text-base",
  );
</script>

<p class="{colorClass} {sizeClass}">{content}</p>
