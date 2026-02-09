<script lang="ts">
	import { Disc, Waves, Sparkles } from 'lucide-svelte';

	interface Props {
		format?: string;
		sampleRate?: number;
		bitDepth?: number;
		bitrate?: number;
		channels?: number;
		compact?: boolean;
	}

	let {
		format = '',
		sampleRate,
		bitDepth,
		bitrate,
		channels,
		compact = false
	}: Props = $props();

	// Normalize format string
	const formatUpper = $derived(format?.toUpperCase() || '');

	// Check if format is lossless
	const isLossless = $derived(
		['FLAC', 'WAV', 'AIFF', 'ALAC', 'APE', 'WV', 'DSD', 'DSF', 'DFF'].includes(formatUpper)
	);

	// Check if Hi-Res (sample rate > 44.1kHz or bit depth > 16)
	const isHiRes = $derived(
		(sampleRate && sampleRate > 48000) || (bitDepth && bitDepth > 16)
	);

	// Format sample rate for display (e.g., 96000 -> "96kHz")
	function formatSampleRate(rate: number | undefined): string {
		if (!rate) return '';
		if (rate >= 1000) {
			return `${(rate / 1000).toFixed(rate % 1000 === 0 ? 0 : 1)}kHz`;
		}
		return `${rate}Hz`;
	}

	// Format bitrate for display (e.g., 320000 -> "320kbps")
	function formatBitrate(rate: number | undefined): string {
		if (!rate) return '';
		if (rate >= 1000) {
			return `${Math.round(rate / 1000)}kbps`;
		}
		return `${rate}bps`;
	}

	// Get format display name
	function getFormatName(fmt: string): string {
		const names: Record<string, string> = {
			FLAC: 'FLAC',
			WAV: 'WAV',
			AIFF: 'AIFF',
			ALAC: 'ALAC',
			APE: 'APE',
			WV: 'WavPack',
			MP3: 'MP3',
			AAC: 'AAC',
			M4A: 'AAC',
			M4B: 'AAC',
			OGG: 'Ogg Vorbis',
			OPUS: 'Opus',
			DSD: 'DSD',
			DSF: 'DSD',
			DFF: 'DSD'
		};
		return names[fmt] || fmt;
	}

	// Build quality string
	const qualityString = $derived(() => {
		const parts: string[] = [];

		if (bitDepth) {
			parts.push(`${bitDepth}-bit`);
		}

		if (sampleRate) {
			parts.push(formatSampleRate(sampleRate));
		}

		if (bitrate && !isLossless) {
			parts.push(formatBitrate(bitrate));
		}

		return parts.join(' / ');
	});

	// Channel display
	const channelString = $derived(() => {
		if (!channels) return '';
		if (channels === 1) return 'Mono';
		if (channels === 2) return 'Stereo';
		if (channels === 6) return '5.1';
		if (channels === 8) return '7.1';
		return `${channels}ch`;
	});
</script>

{#if format}
	<div class="flex items-center gap-2 flex-wrap">
		<!-- Main Format Badge -->
		<span
			class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-mono font-semibold transition-all duration-200 color-transition
				   {isLossless
					   ? 'quality-badge-dynamic shadow-sm'
					   : 'bg-surface-1/80 text-overlay-1 border border-surface-2/50'}"
		>
			{#if isLossless}
				<Waves size={12} class="opacity-80" />
			{:else}
				<Disc size={12} class="opacity-60" />
			{/if}
			{getFormatName(formatUpper)}
		</span>

		<!-- Hi-Res Badge -->
		{#if isHiRes && !compact}
			<span
				class="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-semibold
					   bg-gradient-to-r from-amber-500/20 to-orange-500/20 text-amber-400 border border-amber-500/30"
			>
				<Sparkles size={12} />
				Hi-Res
			</span>
		{/if}

		<!-- Quality Details -->
		{#if !compact && qualityString()}
			<span class="text-xs text-overlay-1 font-mono">
				{qualityString()}
			</span>
		{/if}

		<!-- Channels (if unusual) -->
		{#if !compact && channels && channels !== 2}
			<span class="text-xs text-overlay-0 font-mono">
				{channelString()}
			</span>
		{/if}
	</div>

	<!-- Lossless indicator text -->
	{#if isLossless && !compact}
		<p class="text-xs mt-1 font-medium tracking-wide color-transition" style="color: rgba(var(--dynamic-accent-rgb), 0.7);">
			{#if isHiRes}
				High-Resolution Lossless
			{:else}
				Lossless Audio
			{/if}
		</p>
	{/if}
{/if}
