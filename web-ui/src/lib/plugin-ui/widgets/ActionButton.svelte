<script lang="ts">
  import { getContext } from "svelte";
  import { pluginActions } from "../actions.svelte.js";
  import { toastStore } from "$lib/stores/toast.svelte";

  let {
    label = "",
    action = "",
    params = {},
    style = "secondary",
    confirm = false,
    icon = null,
    disabled = false,
  }: {
    label?: string;
    action?: string;
    params?: Record<string, any>;
    style?: string;
    confirm?: boolean;
    icon?: string | null;
    disabled?: boolean;
  } = $props();

  let loading = $state(false);
  const pluginId = getContext<string>("pluginId");

  const styleMap: Record<string, string> = {
    primary: "bg-accent text-crust hover:opacity-80",
    secondary: "bg-surface-1 text-text hover:bg-surface-2",
    danger: "bg-error text-crust hover:opacity-80",
  };

  const classes = $derived(styleMap[style] ?? styleMap.secondary);

  async function handleClick() {
    if (
      confirm &&
      !window.confirm(`Are you sure you want to ${label.toLowerCase()}?`)
    )
      return;
    loading = true;
    try {
      const result = await pluginActions.dispatch(pluginId, action, params);
      if (result?.message) toastStore.success(result.message);
    } catch (e: any) {
      toastStore.error(`Action failed: ${e.message}`);
    } finally {
      loading = false;
    }
  }
</script>

<button
  class="rounded-lg px-4 py-2 text-sm font-medium transition-all disabled:opacity-50 {classes}"
  onclick={handleClick}
  disabled={loading || disabled}
>
  {#if loading}
    Loading...
  {:else}
    {label}
  {/if}
</button>
