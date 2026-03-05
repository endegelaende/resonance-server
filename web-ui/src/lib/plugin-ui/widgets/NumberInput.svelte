<script lang="ts">
  import { getContext } from "svelte";

  let {
    name = "",
    label = "",
    value = 0,
    min = null,
    max = null,
    step = 1,
    required = false,
    disabled = false,
    help_text = null,
  }: {
    name?: string;
    label?: string;
    value?: number;
    min?: number | null;
    max?: number | null;
    step?: number;
    required?: boolean;
    disabled?: boolean;
    help_text?: string | null;
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

  const isDisabled = $derived(
    disabled || (formContext?.isDisabled?.() ?? false),
  );

  const validationError = $derived.by(() => {
    if (!touched) return null;
    if (required && (localValue === null || localValue === undefined))
      return `${label} is required`;
    if (localValue !== null && localValue !== undefined) {
      if (min !== null && localValue < min)
        return `${label} must be at least ${min}`;
      if (max !== null && localValue > max)
        return `${label} must be at most ${max}`;
    }
    return null;
  });

  $effect(() => {
    formContext?.setValue(name, localValue);
  });
</script>

<div class="flex flex-col gap-1.5">
  {#if label}
    <label for="sdui-{name}" class="text-sm font-medium text-subtext-1">
      {label}
      {#if required}
        <span class="text-error">*</span>
      {/if}
    </label>
  {/if}

  <input
    id="sdui-{name}"
    type="number"
    bind:value={localValue}
    min={min ?? undefined}
    max={max ?? undefined}
    {step}
    disabled={isDisabled}
    required={required}
    class="w-full rounded-lg border bg-surface-0 px-3 py-2 text-sm text-text
      placeholder:text-overlay-0 transition-colors
      focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent
      disabled:opacity-50 disabled:cursor-not-allowed
      [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none
      {validationError
      ? 'border-error focus:ring-error/50 focus:border-error'
      : 'border-border'}"
    onblur={() => (touched = true)}
  />

  {#if validationError}
    <p class="text-xs text-error">{validationError}</p>
  {:else if help_text}
    <p class="text-xs text-overlay-1">{help_text}</p>
  {:else if min !== null || max !== null}
    <p class="text-xs text-overlay-1">
      {#if min !== null && max !== null}
        Range: {min} – {max}
      {:else if min !== null}
        Min: {min}
      {:else if max !== null}
        Max: {max}
      {/if}
    </p>
  {/if}
</div>
