<script lang="ts">
	import { Search, X, Loader2 } from 'lucide-svelte';

	interface Props {
		placeholder?: string;
		onSearch?: (query: string) => void;
		onClear?: () => void;
	}

	let { placeholder = 'Search music...', onSearch, onClear }: Props = $props();

	let query = $state('');
	let isSearching = $state(false);
	let inputElement: HTMLInputElement;
	let debounceTimer: ReturnType<typeof setTimeout> | null = null;

	function handleInput(event: Event) {
		const target = event.target as HTMLInputElement;
		query = target.value;

		// Debounce search
		if (debounceTimer) {
			clearTimeout(debounceTimer);
		}

		if (query.length >= 2) {
			isSearching = true;
			debounceTimer = setTimeout(() => {
				onSearch?.(query);
				isSearching = false;
			}, 300);
		} else if (query.length === 0) {
			onClear?.();
		}
	}

	function handleClear() {
		query = '';
		onClear?.();
		inputElement?.focus();
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			handleClear();
		} else if (event.key === 'Enter' && query.length >= 2) {
			if (debounceTimer) {
				clearTimeout(debounceTimer);
			}
			onSearch?.(query);
			isSearching = false;
		}
	}

	// Focus on keyboard shortcut
	function handleGlobalKeydown(event: KeyboardEvent) {
		if ((event.metaKey || event.ctrlKey) && event.key === 'k') {
			event.preventDefault();
			inputElement?.focus();
		}
	}
</script>

<svelte:window onkeydown={handleGlobalKeydown} />

<div class="relative group">
	<!-- Search Icon -->
	<div class="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none">
		{#if isSearching}
			<Loader2 size={16} class="text-overlay-0 animate-spin" />
		{:else}
			<Search size={16} class="text-overlay-0 group-focus-within:text-overlay-1 transition-colors" />
		{/if}
	</div>

	<!-- Input -->
	<input
		bind:this={inputElement}
		type="text"
		value={query}
		oninput={handleInput}
		onkeydown={handleKeydown}
		{placeholder}
		class="w-full pl-9 pr-9 py-2 bg-surface-0/50 border border-border/40 rounded-lg
			   text-sm text-text placeholder:text-overlay-0
			   focus:outline-none focus:bg-surface-0 focus:border-border
			   transition-all"
		aria-label="Search"
	/>

	<!-- Clear Button -->
	{#if query.length > 0}
		<button
			class="absolute right-3 top-1/2 -translate-y-1/2 p-0.5 rounded-full
				   text-overlay-0 hover:text-text
				   transition-colors"
			onclick={handleClear}
			aria-label="Clear search"
		>
			<X size={14} />
		</button>
	{:else}
		<!-- Keyboard shortcut hint -->
		<div class="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none">
			<kbd class="px-1.5 py-0.5 text-[10px] text-overlay-0/60 bg-surface-0/60 rounded">
				⌘K
			</kbd>
		</div>
	{/if}
</div>
