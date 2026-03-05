<script lang="ts">
  import { getContext } from "svelte";

  let {
    name = "",
    label = "",
    value = false,
    disabled = false,
  }: {
    name?: string;
    label?: string;
    value?: boolean;
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

  const isDisabled = $derived(
    disabled || (formContext?.isDisabled?.() ?? false),
  );

  function toggle() {
    if (isDisabled) return;
    localValue = !localValue;
  }

  $effect(() => {
    formContext?.setValue(name, localValue);
  });
</script>

<div class="flex items-center justify-between gap-3">
  {#if label}
    <label
      for="sdui-toggle-{name}"
      class="text-sm font-medium text-subtext-1 select-none
        {isDisabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}"
    >
      {label}
    </label>
  {/if}

  <button
    id="sdui-toggle-{name}"
    type="button"
    role="switch"
    aria-checked={localValue}
    aria-label={label || name}
    disabled={isDisabled}
    class="relative inline-flex h-6 w-11 shrink-0 rounded-full border-2 border-transparent
      transition-colors duration-200 ease-in-out
      focus:outline-none focus:ring-2 focus:ring-accent/50 focus:ring-offset-2 focus:ring-offset-base
      disabled:opacity-50 disabled:cursor-not-allowed
      {localValue ? 'bg-accent' : 'bg-overlay-0'}"
    onclick={toggle}
  >
    <span
      class="pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-lg
        ring-0 transition-transform duration-200 ease-in-out
        {localValue ? 'translate-x-5' : 'translate-x-0'}"
    ></span>
  </button>
</div>
