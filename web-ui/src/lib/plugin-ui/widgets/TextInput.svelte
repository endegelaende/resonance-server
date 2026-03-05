<script lang="ts">
  import { getContext } from "svelte";

  let {
    name = "",
    label = "",
    value = "",
    placeholder = "",
    required = false,
    pattern = null,
    disabled = false,
  }: {
    name?: string;
    label?: string;
    value?: string;
    placeholder?: string;
    required?: boolean;
    pattern?: string | null;
    disabled?: boolean;
  } = $props();

  const formContext = getContext<
    | {
        setValue: (name: string, value: any) => void;
        isDisabled: () => boolean;
      }
    | undefined
  >("formContext");

  // Intentionally capture initial prop value — this input manages its own state.
  // svelte-ignore state_referenced_locally
  let localValue = $state(value);
  let touched = $state(false);

  const isDisabled = $derived(disabled || (formContext?.isDisabled?.() ?? false));

  const validationError = $derived.by(() => {
    if (!touched) return null;
    if (required && !localValue.trim()) return `${label} is required`;
    if (pattern && localValue) {
      try {
        const re = new RegExp(pattern);
        if (!re.test(localValue)) return `${label} has an invalid format`;
      } catch {
        // invalid regex pattern — skip validation
      }
    }
    return null;
  });

  $effect(() => {
    formContext?.setValue(name, localValue);
  });
</script>

<div class="flex flex-col gap-1.5">
  {#if label}
    <label
      for="sdui-{name}"
      class="text-sm font-medium text-subtext-1"
    >
      {label}
      {#if required}
        <span class="text-error">*</span>
      {/if}
    </label>
  {/if}

  <input
    id="sdui-{name}"
    type="text"
    bind:value={localValue}
    {placeholder}
    disabled={isDisabled}
    required={required}
    class="w-full rounded-lg border bg-surface-0 px-3 py-2 text-sm text-text
      placeholder:text-overlay-0 transition-colors
      focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent
      disabled:opacity-50 disabled:cursor-not-allowed
      {validationError
      ? 'border-error focus:ring-error/50 focus:border-error'
      : 'border-border'}"
    onblur={() => (touched = true)}
  />

  {#if validationError}
    <p class="text-xs text-error">{validationError}</p>
  {/if}
</div>
