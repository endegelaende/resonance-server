<script lang="ts">
  import { getContext } from "svelte";

  let {
    name = "",
    label = "",
    value = "",
    options = [],
    required = false,
    disabled = false,
  }: {
    name?: string;
    label?: string;
    value?: string;
    options?: Array<{ value: string; label: string }>;
    required?: boolean;
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

  const isDisabled = $derived(
    disabled || (formContext?.isDisabled?.() ?? false),
  );

  const validationError = $derived.by(() => {
    if (!touched) return null;
    if (required && !localValue) return `${label} is required`;
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

  <div class="relative">
    <select
      id="sdui-{name}"
      bind:value={localValue}
      disabled={isDisabled}
      required={required}
      class="w-full appearance-none rounded-lg border bg-surface-0 px-3 py-2 pr-8 text-sm text-text
        transition-colors cursor-pointer
        focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent
        disabled:opacity-50 disabled:cursor-not-allowed
        {validationError
        ? 'border-error focus:ring-error/50 focus:border-error'
        : 'border-border'}"
      onblur={() => (touched = true)}
      onchange={() => (touched = true)}
    >
      {#each options as opt}
        <option value={opt.value}>{opt.label}</option>
      {/each}
    </select>

    <!-- Dropdown chevron -->
    <div
      class="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2.5 text-overlay-1"
    >
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="m6 9 6 6 6-6" />
      </svg>
    </div>
  </div>

  {#if validationError}
    <p class="text-xs text-error">{validationError}</p>
  {/if}
</div>
