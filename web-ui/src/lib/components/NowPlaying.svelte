<script lang="ts">
	import { playerStore } from '$lib/stores/player.svelte';
	import { colorStore } from '$lib/stores/color.svelte';
	import {
		Play,
		Pause,
		SkipBack,
		SkipForward,
		Volume2,
		VolumeX,
		Maximize2,
		Radio
	} from 'lucide-svelte';
	import QualityBadge from './QualityBadge.svelte';
	import CoverArt from './CoverArt.svelte';

	// Track cover art changes and extract colors
	$effect(() => {
		const coverArt = playerStore.currentTrack?.coverArt;
		colorStore.setFromImage(coverArt);
	});

	// ── Radio / Live stream detection ────────────────────────────
	// A track is "radio" when the backend flags it as a remote live
	// stream (source === "radio" or isLive === true).
	const isRadio = $derived(
		playerStore.currentTrack?.source === 'radio' ||
		playerStore.currentTrack?.source === 'podcast'
	);
	const isLive = $derived(
		Boolean(playerStore.currentTrack?.isLive)
	);

	// Display strings for radio mode (ICY metadata)
	// - stationName: static title (= station/show name)
	// - icyNowPlaying: full ICY StreamTitle ("Artist - Title" or freeform)
	// - icyArtist / icyTitle: parsed from ICY when exactly one " - " separator
	const stationName = $derived(playerStore.currentTrack?.title ?? '');
	const icyNowPlaying = $derived(playerStore.currentTrack?.currentTitle ?? '');
	const icyArtist = $derived(playerStore.currentTrack?.icyArtist ?? '');
	const icyTitle = $derived(playerStore.currentTrack?.icyTitle ?? '');

	// Has parsed ICY data (artist + title split)?
	const hasIcyParsed = $derived(Boolean(icyArtist && icyTitle));
	// Has any ICY data at all?
	const hasIcyData = $derived(
		Boolean(icyNowPlaying) && icyNowPlaying !== stationName
	);

	// Format seconds to mm:ss
	function formatTime(seconds: number): string {
		if (!seconds || seconds < 0) return '0:00';
		const mins = Math.floor(seconds / 60);
		const secs = Math.floor(seconds % 60);
		return `${mins}:${secs.toString().padStart(2, '0')}`;
	}

	// Handle progress bar click (disabled for live streams)
	function handleSeek(event: MouseEvent) {
		if (isLive) return;
		const target = event.currentTarget as HTMLDivElement;
		const rect = target.getBoundingClientRect();
		const percent = (event.clientX - rect.left) / rect.width;
		const newTime = percent * playerStore.status.duration;
		playerStore.seek(newTime);
	}

	// Map content_type to a human-readable format string for radio
	function getRadioFormat(contentType?: string): string {
		if (!contentType) return '';
		const map: Record<string, string> = {
			'audio/mpeg': 'MP3',
			'audio/mp3': 'MP3',
			'audio/aac': 'AAC',
			'audio/aacp': 'AAC+',
			'audio/ogg': 'OGG',
			'audio/flac': 'FLAC',
			'audio/x-flac': 'FLAC',
		};
		return map[contentType.toLowerCase()] || contentType.split('/').pop()?.toUpperCase() || '';
	}

	// Volume preview state
	let volumePreview = $state<number | null>(null);
	let showVolumePreview = $state(false);
	let isDraggingVolume = $state(false);
	let previewVolume = $state(0);

	// Handle volume drag start
	function handleVolumeStart() {
		isDraggingVolume = true;
	}

	// Handle volume slider - live update while dragging
	function handleVolumeInput(event: Event) {
		const target = event.target as HTMLInputElement;
		previewVolume = parseInt(target.value);
		volumePreview = previewVolume;
		showVolumePreview = true;
	}

	// Handle volume slider - commit on release
	function handleVolumeChange(event: Event) {
		const target = event.target as HTMLInputElement;
		playerStore.setVolume(parseInt(target.value));
		showVolumePreview = false;
		volumePreview = null;
		isDraggingVolume = false;
	}

	// Get file extension from path for format badge
	function getFormat(path: string | undefined): string {
		if (!path) return '';
		const ext = path.split('.').pop()?.toUpperCase() || '';
		return ext;
	}
</script>

<div class="relative rounded-xl overflow-hidden color-transition">
	<!-- UltraBlur Background Layer -->
	{#if playerStore.currentTrack?.coverArt}
		<div class="absolute inset-0 -z-10">
			<!-- Blurred album art background -->
			<img
				src={playerStore.currentTrack.coverArt}
				alt=""
				class="absolute inset-0 w-full h-full object-cover scale-150 blur-3xl opacity-40"
				aria-hidden="true"
			/>
			<!-- Gradient overlay for readability -->
			<div class="absolute inset-0 bg-gradient-to-t from-base/90 via-base/70 to-base/50"></div>
		</div>
	{:else}
		<!-- Fallback gradient when no artwork -->
		<div class="absolute inset-0 -z-10 bg-gradient-to-br from-surface-0 to-base"></div>
	{/if}

	<!-- Content -->
	<div class="relative glass rounded-xl p-6 flex flex-col gap-6 backdrop-blur-sm bg-base/30 border border-white/5 color-transition">
		<!-- Album Art & Track Info -->
		<div class="flex gap-6 items-center">
			<!-- Album Art with Glow using CoverArt component -->
			<div class="relative shrink-0 group">
				<!-- Glow effect behind album art - uses dynamic accent color -->
				{#if playerStore.currentTrack?.coverArt}
					<div
						class="absolute inset-0 rounded-lg blur-xl opacity-60 group-hover:opacity-80 transition-all duration-500 dynamic-glow"
						style="background-color: var(--dynamic-accent); transform: scale(1.1);"
					></div>
				{/if}

				<!-- Album art container with BlurHash support -->
				<div
					class="relative w-32 h-32 rounded-lg overflow-hidden shadow-2xl ring-1 ring-white/10 color-transition"
					style="box-shadow: 0 25px 50px -12px rgba(var(--dynamic-accent-rgb), 0.25);"
				>
					<CoverArt
						src={playerStore.currentTrack?.coverArt}
						blurhash={playerStore.currentTrack?.blurhash}
						alt="Album art"
						size="full"
						showDisc={true}
						spinning={playerStore.isPlaying}
						hoverScale={true}
					/>
				</div>

				<!-- Fullscreen button overlay -->
				<button
					class="absolute inset-0 flex items-center justify-center bg-black/0 hover:bg-black/40 rounded-lg opacity-0 group-hover:opacity-100 transition-all duration-200"
					aria-label="Fullscreen"
				>
					<Maximize2 size={24} class="text-white drop-shadow-lg" />
				</button>
			</div>

			<!-- Track Info -->
			<div class="flex flex-col gap-1 min-w-0 flex-1">
				{#if playerStore.currentTrack}
					{#if isRadio || isLive}
						<!-- ── Radio / Live Stream Mode ──────────────── -->
						<!-- Station name as primary heading -->
						<div class="flex items-center gap-2">
							<h2 class="text-xl font-semibold text-text truncate drop-shadow-sm">
								{stationName}
							</h2>
							{#if isLive}
								<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold uppercase tracking-wider bg-red-500/90 text-white shadow-sm shadow-red-500/30 shrink-0 animate-pulse">
									<Radio size={12} />
									LIVE
								</span>
							{/if}
						</div>

						<!-- ICY "Now Playing" — parsed artist + title or raw string -->
						{#if hasIcyParsed}
							<p class="text-subtext-0 truncate" title={icyNowPlaying}>
								{icyArtist}
							</p>
							<p class="text-overlay-1 text-sm truncate" title={icyNowPlaying}>
								{icyTitle}
							</p>
						{:else if hasIcyData}
							<p class="text-subtext-0 truncate" title={icyNowPlaying}>
								{icyNowPlaying}
							</p>
						{:else}
							<p class="text-overlay-1 text-sm truncate italic">
								Listening…
							</p>
						{/if}

						<!-- Quality Badge for radio — use contentType + bitrate -->
						<div class="mt-2">
							<QualityBadge
								format={getRadioFormat(playerStore.currentTrack.contentType)}
								bitrate={playerStore.currentTrack.bitrate}
							/>
						</div>
					{:else}
						<!-- ── Local / Normal Track Mode ─────────────── -->
						<h2 class="text-xl font-semibold text-text truncate drop-shadow-sm">
							{playerStore.currentTrack.title}
						</h2>
						<p class="text-subtext-0 truncate">
							{playerStore.currentTrack.artist}
						</p>
						<p class="text-overlay-1 text-sm truncate">
							{playerStore.currentTrack.album}
						</p>

						<!-- Quality Badge -->
						{#if playerStore.currentTrack.path}
							<div class="mt-2">
								<QualityBadge
									format={getFormat(playerStore.currentTrack.path)}
									sampleRate={playerStore.currentTrack.sampleRate}
									bitDepth={playerStore.currentTrack.bitDepth}
									bitrate={playerStore.currentTrack.bitrate}
									channels={playerStore.currentTrack.channels}
								/>
							</div>
						{/if}
					{/if}
				{:else}
					<h2 class="text-xl font-semibold text-overlay-0">No track playing</h2>
					<p class="text-overlay-0">Select a track to start</p>
				{/if}
			</div>
		</div>

		<!-- Progress Bar / Live Indicator -->
		{#if isLive}
			<!-- Live stream: no seekable progress, show elapsed listening time -->
			<div class="flex flex-col gap-2">
				<div class="w-full h-2 bg-surface-1/50 rounded-full overflow-hidden backdrop-blur-sm">
					<!-- Animated "streaming" bar -->
					<div
						class="h-full rounded-full dynamic-progress animate-live-bar"
						style="width: 100%; opacity: 0.6;"
					></div>
				</div>

				<style>
					/* Subtle pulsing animation for the live streaming bar */
					@keyframes live-bar-pulse {
						0%, 100% { opacity: 0.4; }
						50% { opacity: 0.7; }
					}
					:global(.animate-live-bar) {
						animation: live-bar-pulse 2s ease-in-out infinite;
					}
				</style>
				<div class="flex justify-between text-sm text-overlay-1">
					<span class="font-mono text-xs">{formatTime(playerStore.elapsedTime)}</span>
					<span class="font-mono text-xs uppercase tracking-wide text-red-400/80">Live</span>
				</div>
			</div>
		{:else}
			<!-- Normal track: seekable progress bar -->
			<div class="flex flex-col gap-2">
				<button
					class="w-full h-2 bg-surface-1/50 rounded-full cursor-pointer overflow-hidden group backdrop-blur-sm"
					onclick={handleSeek}
					aria-label="Seek"
				>
					<!-- Progress fill with dynamic gradient -->
					<div
						class="h-full rounded-full transition-all duration-150 dynamic-progress group-hover:shadow-[0_0_12px_rgba(var(--dynamic-accent-rgb),0.5)]"
						style="width: {playerStore.progress}%"
					></div>
				</button>
				<div class="flex justify-between text-sm text-overlay-1">
					<span class="font-mono text-xs">{formatTime(playerStore.elapsedTime)}</span>
					<span class="font-mono text-xs">{formatTime(playerStore.status.duration)}</span>
				</div>
			</div>
		{/if}

		<!-- Controls -->
		<div class="flex items-center justify-between">
			<!-- Playback Controls -->
			<div class="flex items-center gap-4">
				<button
					class="p-2 rounded-full hover:bg-white/10 text-text transition-all duration-200 hover:scale-105 active:scale-95"
					onclick={() => playerStore.previous()}
					aria-label="Previous"
				>
					<SkipBack size={24} />
				</button>

				<button
					class="p-4 rounded-full text-crust transition-all duration-200 shadow-lg hover:shadow-xl hover:scale-105 active:scale-95 dynamic-btn"
					onclick={() => playerStore.togglePlayPause()}
					aria-label={playerStore.isPlaying ? 'Pause' : 'Play'}
				>
					{#if playerStore.isPlaying}
						<Pause size={28} fill="currentColor" />
					{:else}
						<Play size={28} fill="currentColor" />
					{/if}
				</button>

				<button
					class="p-2 rounded-full hover:bg-white/10 text-text transition-all duration-200 hover:scale-105 active:scale-95"
					onclick={() => playerStore.next()}
					aria-label="Next"
				>
					<SkipForward size={24} />
				</button>
			</div>

			<!-- Volume -->
			<div class="flex items-center gap-3 relative">
				<button
					class="p-2 rounded-full hover:bg-white/10 text-text transition-all duration-200"
					onclick={() => playerStore.toggleMute()}
					aria-label={playerStore.status.muted ? 'Unmute' : 'Mute'}
				>
					{#if playerStore.status.muted || playerStore.status.volume === 0}
						<VolumeX size={20} />
					{:else}
						<Volume2 size={20} />
					{/if}
				</button>

				<div class="relative">
						<input
							type="range"
							min="0"
							max="100"
							value={playerStore.status.volume}
							oninput={handleVolumeInput}
							onchange={handleVolumeChange}
							onmousedown={handleVolumeStart}
							ontouchstart={handleVolumeStart}
							class="w-24 h-2 bg-surface-1/50 rounded-full appearance-none cursor-pointer backdrop-blur-sm color-transition
								   [&::-webkit-slider-thumb]:appearance-none
								   [&::-webkit-slider-thumb]:w-4
								   [&::-webkit-slider-thumb]:h-4
								   [&::-webkit-slider-thumb]:rounded-full
								   [&::-webkit-slider-thumb]:transition-all
								   [&::-webkit-slider-thumb]:shadow-lg
								   [&::-webkit-slider-thumb]:hover:scale-110"
							style="--tw-slider-thumb-bg: var(--dynamic-accent);"
							aria-label="Volume"
						/>
					<span class="text-sm text-overlay-1 w-10 text-right font-mono tabular-nums">
						{String(isDraggingVolume ? previewVolume : playerStore.status.volume).padStart(3, '\u00A0')}
					</span>
				</div>
			</div>
		</div>
	</div>
</div>
