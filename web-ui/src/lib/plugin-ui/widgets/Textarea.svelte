<script lang="ts">
  import { getContext } from "svelte";

  let {
    name = "",
    label = "",
    value = "",
    placeholder = "",
    rows = 4,
    maxlength = null,
    required = false,
    disabled = false,
    help_text = null,
  }: {
    name?: string;
    label?: string;
    value?: string;
    placeholder?: string;
    rows?: number;
    maxlength?: number | null;
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

  const isDisabled = $derived(disabled || (formContext?.isDisabled?.() ?? false));

  const validationError = $derived.by(() => {
    if (!touched) return null;
    if (required && !localValue.trim()) return `${label} is required`;
    if (maxlength !== null && localValue.length > maxlength)
      return `${label} must be at most ${maxlength} characters`;
    return null;
  });

  const charCount = $derived(
    maxlength !== null ? `${localValue.length} / ${maxlength}` : null,
  );

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

  <textarea
    id="sdui-{name}"
    bind:value={localValue}
    {placeholder}
    {rows}
    maxlength={maxlength ?? undefined}
    disabled={isDisabled}
    required={required}
    class="w-full rounded-lg border bg-surface-0 px-3 py-2 text-sm text-text
      placeholder:text-overlay-0 transition-colors resize-y
      focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent
      disabled:opacity-50 disabled:cursor-not-allowed
      {validationError
      ? 'border-error focus:ring-error/50 focus:border-error'
      : 'border-border'}"
    onblur={() => (touched = true)}
  ></textarea>

  <div class="flex items-center justify-between">
    {#if validationError}
      <p class="text-xs text-error">{validationError}</p>
    {:else if help_text}
      <p class="text-xs text-overlay-1">{help_text}</p>
    {:else}
      <span></span>
    {/if}

    {#if charCount}
      <p class="text-xs text-overlay-1 ml-auto">{charCount}</p>
    {/if}
  </div>
</div>
