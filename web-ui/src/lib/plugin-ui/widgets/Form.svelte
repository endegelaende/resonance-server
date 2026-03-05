<script lang="ts">
  import { getContext, setContext } from "svelte";
  import { pluginActions } from "../actions.svelte.js";
  import { toastStore } from "$lib/stores/toast.svelte";
  import type { Snippet } from "svelte";

  let {
    action = "",
    submit_label = "Save",
    submit_style = "primary",
    disabled = false,
    children,
  }: {
    action?: string;
    submit_label?: string;
    submit_style?: string;
    disabled?: boolean;
    children?: Snippet;
  } = $props();

  const pluginId = getContext<string>("pluginId");

  let formValues: Record<string, any> = $state({});
  let loading = $state(false);
  let dirty = $state(false);

  // Track initial values to detect changes
  let initialValues: Record<string, any> = {};
  let initialised = false;

  const styleMap: Record<string, string> = {
    primary: "bg-accent text-crust hover:opacity-80",
    secondary: "bg-surface-1 text-text hover:bg-surface-2",
    danger: "bg-error text-crust hover:opacity-80",
  };

  const buttonClasses = $derived(styleMap[submit_style] ?? styleMap.primary);

  // Provide form context so child input widgets can register their values
  // and PluginRenderer can check visible_when conditions.
  setContext("formContext", {
    setValue: (name: string, value: any) => {
      formValues[name] = value;

      // Capture initial values on first registration
      if (!initialised) {
        initialValues[name] = value;
      }

      // After initialisation, track dirtiness
      if (initialised) {
        dirty = Object.keys(formValues).some(
          (k) => formValues[k] !== initialValues[k],
        );
      }
    },
    isDisabled: () => disabled || loading,
    getValues: () => formValues,
  });

  // Mark initialisation complete after first tick (all children have registered)
  $effect(() => {
    if (!initialised && Object.keys(formValues).length > 0) {
      // Snapshot initial state
      initialValues = { ...formValues };
      initialised = true;
    }
  });

  async function handleSubmit(e: Event) {
    e.preventDefault();
    if (disabled || loading) return;

    loading = true;
    try {
      const result = await pluginActions.dispatch(
        pluginId,
        action,
        { ...formValues },
      );
      if (result?.message) {
        toastStore.success(result.message);
      }
      // Reset dirty tracking after successful save
      initialValues = { ...formValues };
      dirty = false;
    } catch (err: any) {
      toastStore.error(`Action failed: ${err.message}`);
    } finally {
      loading = false;
    }
  }
</script>

<form
  class="flex flex-col gap-4"
  onsubmit={handleSubmit}
>
  {#if children}
    {@render children()}
  {/if}

  <!-- Submit row -->
  <div class="flex items-center gap-3 pt-2">
    <button
      type="submit"
      class="rounded-lg px-4 py-2 text-sm font-medium transition-all
        disabled:opacity-50 disabled:cursor-not-allowed {buttonClasses}"
      disabled={disabled || loading || !dirty}
    >
      {#if loading}
        <span class="inline-flex items-center gap-2">
          <svg
            class="h-4 w-4 animate-spin"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
          </svg>
          Saving…
        </span>
      {:else}
        {submit_label}
      {/if}
    </button>

    {#if dirty && !loading}
      <span class="text-xs text-overlay-1 italic">Unsaved changes</span>
    {/if}
  </div>
</form>
