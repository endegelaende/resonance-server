<script lang="ts">
	import { decode } from 'blurhash';

	/**
	 * BlurHashPlaceholder Component
	 *
	 * Renders a blurred placeholder image from a BlurHash string.
	 * Used while the actual cover art is loading for a smooth, elegant UX.
	 *
	 * Usage:
	 * <BlurHashPlaceholder blurhash="LEHV6nWB2yk8pyo0adR*.7kCMdnj" />
	 */

	interface Props {
		/** BlurHash string (20-30 characters) */
		blurhash: string | null | undefined;
		/** Width of the rendered placeholder */
		width?: number;
		/** Height of the rendered placeholder */
		height?: number;
		/** Punch factor for color vibrancy (1 = normal, >1 = more vibrant) */
		punch?: number;
		/** CSS class to apply to the canvas */
		class?: string;
	}

	let {
		blurhash,
		width = 32,
		height = 32,
		punch = 1,
		class: className = ''
	}: Props = $props();

	let canvas: HTMLCanvasElement | null = $state(null);

	// Decode BlurHash and render to canvas when blurhash or dimensions change
	$effect(() => {
		if (!canvas || !blurhash) return;

		try {
			// Decode BlurHash to pixel array
			// Using smaller decode size for performance, CSS will scale it
			const decodeWidth = Math.min(width, 32);
			const decodeHeight = Math.min(height, 32);

			const pixels = decode(blurhash, decodeWidth, decodeHeight, punch);

			// Set canvas dimensions
			canvas.width = decodeWidth;
			canvas.height = decodeHeight;

			// Get 2D context and create image data
			const ctx = canvas.getContext('2d');
			if (!ctx) return;

			const imageData = ctx.createImageData(decodeWidth, decodeHeight);
			imageData.data.set(pixels);
			ctx.putImageData(imageData, 0, 0);
		} catch (e) {
			// Invalid BlurHash string - silently fail
			console.debug('BlurHash decode failed:', e);
		}
	});
</script>

{#if blurhash}
	<canvas
		bind:this={canvas}
		class="blurhash-placeholder {className}"
		style="width: 100%; height: 100%; object-fit: cover;"
		aria-hidden="true"
	></canvas>
{/if}

<style>
	.blurhash-placeholder {
		/* Smooth scaling for the low-res decoded image */
		image-rendering: auto;
		/* Ensure it fills the container */
		display: block;
	}
</style>
