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
			<Loader2 size={18} class="text-overlay-1 animate-spin" />
		{:else}
			<Search size={18} class="text-overlay-1 group-focus-within:text-accent transition-colors" />
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
		class="w-full pl-10 pr-10 py-2.5 bg-surface-0 border border-border rounded-lg
			   text-text placeholder:text-overlay-0
			   focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent
			   transition-all"
		aria-label="Search"
	/>

	<!-- Clear Button -->
	{#if query.length > 0}
		<button
			class="absolute right-3 top-1/2 -translate-y-1/2 p-1 rounded-full
				   text-overlay-1 hover:text-text hover:bg-surface-1
				   transition-colors"
			onclick={handleClear}
			aria-label="Clear search"
		>
			<X size={16} />
		</button>
	{:else}
		<!-- Keyboard shortcut hint -->
		<div class="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none">
			<kbd class="px-1.5 py-0.5 text-xs text-overlay-0 bg-surface-1 rounded border border-border">
				⌘K
			</kbd>
		</div>
	{/if}
</div>
