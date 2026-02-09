<script lang="ts">
	import { Disc3 } from 'lucide-svelte';
	import BlurHashPlaceholder from './BlurHashPlaceholder.svelte';

	/**
	 * CoverArt Component
	 *
	 * Displays album/track artwork with BlurHash placeholder support.
	 * Shows a blurred preview while the full image loads, then fades in
	 * the actual image for a smooth, polished experience.
	 *
	 * Usage:
	 * <CoverArt
	 *   src="/api/artwork/album/123"
	 *   blurhash="LEHV6nWB2yk8pyo0adR*.7kCMdnj"
	 *   alt="Album Name"
	 * />
	 */

	interface Props {
		/** Image source URL */
		src: string | null | undefined;
		/** BlurHash string for placeholder */
		blurhash?: string | null;
		/** Alt text for accessibility */
		alt?: string;
		/** Size variant */
		size?: 'sm' | 'md' | 'lg' | 'xl' | 'full';
		/** Show spinning disc when no artwork */
		showDisc?: boolean;
		/** Whether disc should spin (when playing) */
		spinning?: boolean;
		/** Additional CSS classes */
		class?: string;
		/** Enable glow effect */
		glow?: boolean;
		/** Enable hover scale effect */
		hoverScale?: boolean;
	}

	let {
		src,
		blurhash = null,
		alt = 'Cover art',
		size = 'md',
		showDisc = true,
		spinning = false,
		class: className = '',
		glow = false,
		hoverScale = false
	}: Props = $props();

	// Track image loading state
	let imageLoaded = $state(false);
	let imageError = $state(false);

	// Reset loading state when src changes
	$effect(() => {
		if (src) {
			imageLoaded = false;
			imageError = false;
		}
	});

	// Size classes
	const sizeClasses: Record<string, string> = {
		sm: 'w-10 h-10',
		md: 'w-16 h-16',
		lg: 'w-24 h-24',
		xl: 'w-32 h-32',
		full: 'w-full h-full'
	};

	// Disc sizes
	const discSizes: Record<string, number> = {
		sm: 20,
		md: 32,
		lg: 48,
		xl: 64,
		full: 64
	};

	function handleLoad() {
		imageLoaded = true;
	}

	function handleError() {
		imageError = true;
		imageLoaded = false;
	}

	const hasValidSrc = $derived(src && !imageError);
	const showPlaceholder = $derived(blurhash && !imageLoaded && hasValidSrc);
	const showImage = $derived(hasValidSrc);
	const showFallback = $derived(!hasValidSrc && showDisc);
</script>

<div
	class="cover-art relative overflow-hidden rounded-lg bg-surface-0 flex items-center justify-center {sizeClasses[size]} {className}"
	class:group={hoverScale}
>
	<!-- Glow effect layer -->
	{#if glow && hasValidSrc}
		<div
			class="absolute inset-0 rounded-lg blur-xl opacity-60 transition-opacity duration-500 -z-10"
			class:group-hover:opacity-80={hoverScale}
			style="background-color: var(--dynamic-accent, #666); transform: scale(1.1);"
		></div>
	{/if}

	<!-- BlurHash placeholder layer -->
	{#if showPlaceholder}
		<div class="absolute inset-0 z-10 transition-opacity duration-300" class:opacity-0={imageLoaded}>
			<BlurHashPlaceholder {blurhash} class="rounded-lg" />
		</div>
	{/if}

	<!-- Actual image -->
	{#if showImage}
		<img
			{src}
			{alt}
			class="absolute inset-0 w-full h-full object-cover rounded-lg transition-all duration-300"
			class:opacity-0={!imageLoaded}
			class:opacity-100={imageLoaded}
			class:group-hover:scale-105={hoverScale && imageLoaded}
			onload={handleLoad}
			onerror={handleError}
		/>
	{/if}

	<!-- Fallback disc icon -->
	{#if showFallback}
		<Disc3
			size={discSizes[size]}
			class="text-overlay-0 {spinning ? 'animate-spin-slow' : ''}"
		/>
	{/if}
</div>

<style>
	.cover-art {
		/* Ensure proper stacking */
		isolation: isolate;
	}

	/* Smooth slow spin animation for disc */
	:global(.animate-spin-slow) {
		animation: spin 3s linear infinite;
	}

	@keyframes spin {
		from {
			transform: rotate(0deg);
		}
		to {
			transform: rotate(360deg);
		}
	}
</style>
