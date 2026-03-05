<script lang="ts">
  import { getContext } from "svelte";
  import { registry } from "./registry.js";
  import Self from "./PluginRenderer.svelte";

  let { component }: { component: any } = $props();

  const Widget = $derived(registry[component.type]);

  // Form context for visible_when conditional rendering.
  // This is set by Form.svelte and provides getValues() to read current field values.
  const formContext = getContext<
    | {
        setValue: (name: string, value: any) => void;
        isDisabled: () => boolean;
        getValues: () => Record<string, any>;
      }
    | undefined
  >("formContext");

  // Evaluate visible_when condition against current form state.
  // When no condition is set, the component is always visible.
  // When a condition is set but there is no form context, the component is always visible.
  // Supported operators: eq (default), ne, gt, lt, gte, lte, in, not_in.
  const isVisible = $derived.by(() => {
    const cond = component.visible_when;
    if (!cond || !cond.field) return true;
    if (!formContext?.getValues) return true;

    const values = formContext.getValues();
    const currentValue = values[cond.field];
    const op: string = cond.operator ?? "eq";

    switch (op) {
      case "eq":
        return currentValue === cond.value;
      case "ne":
        return currentValue !== cond.value;
      case "gt":
        return currentValue > cond.value;
      case "lt":
        return currentValue < cond.value;
      case "gte":
        return currentValue >= cond.value;
      case "lte":
        return currentValue <= cond.value;
      case "in":
        return Array.isArray(cond.value) && cond.value.includes(currentValue);
      case "not_in":
        return Array.isArray(cond.value) && !cond.value.includes(currentValue);
      default:
        // Unknown operator — show the component (fail-open)
        return true;
    }
  });
</script>

{#if isVisible}
  {#if Widget}
    {#if component.children && component.children.length > 0}
      <Widget {...component.props}>
        {#each component.children as child}
          <Self component={child} />
        {/each}
      </Widget>
    {:else}
      <Widget {...component.props} />
    {/if}
  {:else}
    <div
      class="rounded-lg border border-overlay-0 bg-surface-0 p-3 text-subtext-0 text-sm"
    >
      <p>Unknown widget: <code>{component.type}</code></p>
      {#if component.fallback_text}
        <p class="mt-1 text-overlay-1">{component.fallback_text}</p>
      {/if}
    </div>
  {/if}
{/if}
