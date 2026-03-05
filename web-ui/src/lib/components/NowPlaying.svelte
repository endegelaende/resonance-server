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
	const isRadio = $derived(
		playerStore.currentTrack?.source === 'radio' ||
		playerStore.currentTrack?.source === 'podcast'
	);
	const isLive = $derived(
		Boolean(playerStore.currentTrack?.isLive)
	);

	// Display strings for radio mode (ICY metadata)
	const stationName = $derived(playerStore.currentTrack?.title ?? '');
	const icyNowPlaying = $derived(playerStore.currentTrack?.currentTitle ?? '');
	const icyArtist = $derived(playerStore.currentTrack?.icyArtist ?? '');
	const icyTitle = $derived(playerStore.currentTrack?.icyTitle ?? '');

	const hasIcyParsed = $derived(Boolean(icyArtist && icyTitle));
	const hasIcyData = $derived(
		Boolean(icyNowPlaying) && icyNowPlaying !== stationName
	);

	function formatTime(seconds: number): string {
		if (!seconds || seconds < 0) return '0:00';
		const mins = Math.floor(seconds / 60);
		const secs = Math.floor(seconds % 60);
		return `${mins}:${secs.toString().padStart(2, '0')}`;
	}

	function handleSeek(event: MouseEvent) {
		if (isLive) return;
		const target = event.currentTarget as HTMLDivElement;
		const rect = target.getBoundingClientRect();
		const percent = (event.clientX - rect.left) / rect.width;
		const newTime = percent * playerStore.status.duration;
		playerStore.seek(newTime);
	}

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

	// Volume
	let isDraggingVolume = $state(false);
	let previewVolume = $state(0);

	function handleVolumeStart() {
		isDraggingVolume = true;
	}

	function handleVolumeInput(event: Event) {
		const target = event.target as HTMLInputElement;
		previewVolume = parseInt(target.value);
	}

	function handleVolumeChange(event: Event) {
		const target = event.target as HTMLInputElement;
		playerStore.setVolume(parseInt(target.value));
		isDraggingVolume = false;
	}

	function getFormat(path: string | undefined): string {
		if (!path) return '';
		return path.split('.').pop()?.toUpperCase() || '';
	}

	// Computed volume for the slider fill
	let displayVolume = $derived(isDraggingVolume ? previewVolume : playerStore.status.volume);
</script>

<!-- Wrapper: fills available space, uses flexbox to arrange content -->
<div class="relative w-full h-full overflow-hidden">

	<!-- ── Ambient background: blurred cover art ── -->
	{#if playerStore.currentTrack?.coverArt}
		<div class="absolute inset-0 -z-10 overflow-hidden">
			<img
				src={playerStore.currentTrack.coverArt}
				alt=""
				class="absolute inset-0 w-full h-full object-cover scale-125 blur-[80px] opacity-25 saturate-150"
				aria-hidden="true"
			/>
			<div class="absolute inset-0 bg-gradient-to-t from-base via-base/80 to-base/40"></div>
		</div>
	{/if}

	<!-- ── Content layout ── -->
	<div class="relative flex items-center gap-8 h-full px-6 py-4">

		<!-- Album Art — hero element -->
		<div class="relative shrink-0 self-center group">
			<!-- Soft ambient glow behind art -->
			{#if playerStore.currentTrack?.coverArt}
				<div
					class="absolute -inset-3 rounded-2xl opacity-40 blur-2xl transition-all duration-700 group-hover:opacity-55"
					style="background-color: var(--dynamic-accent);"
				></div>
			{/if}

			<div
				class="relative rounded-xl overflow-hidden shadow-2xl transition-all duration-500
					   ring-1 ring-white/[0.06]"
				style="width: clamp(100px, calc(100% - 2rem), 220px);
					   height: clamp(100px, calc(100% - 2rem), 220px);
					   aspect-ratio: 1;
					   box-shadow: 0 20px 60px -15px rgba(var(--dynamic-accent-rgb), 0.2);"
			>
				<CoverArt
					src={playerStore.currentTrack?.coverArt}
					blurhash={playerStore.currentTrack?.blurhash}
					alt="Album art"
					size="full"
					showDisc={true}
					spinning={playerStore.isPlaying}
					hoverScale={false}
				/>
			</div>
		</div>

		<!-- Track info + controls — fills remaining space -->
		<div class="flex-1 flex flex-col justify-center gap-3 min-w-0 min-h-0">

			<!-- Track metadata -->
			<div class="min-w-0">
				{#if playerStore.currentTrack}
					{#if isRadio || isLive}
						<!-- Radio / stream mode -->
						<div class="flex items-center gap-2.5 mb-1">
							<h2 class="text-lg font-semibold text-text truncate leading-tight">
								{stationName}
							</h2>
							{#if isLive}
								<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-widest bg-red-500/80 text-white shrink-0">
									<Radio size={10} />
									Live
								</span>
							{/if}
						</div>

						{#if hasIcyParsed}
							<p class="text-sm text-subtext-0 truncate">{icyArtist}</p>
							<p class="text-xs text-overlay-1 truncate mt-0.5">{icyTitle}</p>
						{:else if hasIcyData}
							<p class="text-sm text-subtext-0 truncate">{icyNowPlaying}</p>
						{:else}
							<p class="text-xs text-overlay-1 italic mt-0.5">Listening…</p>
						{/if}

						<div class="mt-2">
							<QualityBadge
								format={getRadioFormat(playerStore.currentTrack.contentType)}
								bitrate={playerStore.currentTrack.bitrate}
							/>
						</div>
					{:else}
						<!-- Normal track mode -->
						<h2 class="text-lg font-semibold text-text truncate leading-tight">
							{playerStore.currentTrack.title}
						</h2>
						<p class="text-sm text-subtext-0 truncate mt-0.5">
							{playerStore.currentTrack.artist}
						</p>
						<p class="text-xs text-overlay-1 truncate mt-0.5">
							{playerStore.currentTrack.album}
						</p>

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
					<!-- Empty state -->
					<h2 class="text-base text-overlay-1 font-medium">No track playing</h2>
					<p class="text-xs text-overlay-0 mt-1">Select a track to start</p>
				{/if}
			</div>

			<!-- Progress bar -->
			{#if playerStore.currentTrack}
				<div class="flex flex-col gap-1.5 mt-1">
					{#if isLive}
						<!-- Live: animated streaming indicator -->
						<div class="w-full h-1 bg-surface-1/40 rounded-full overflow-hidden">
							<div class="h-full rounded-full animate-live-bar dynamic-progress" style="width: 100%; opacity: 0.5;"></div>
						</div>
						<div class="flex justify-between text-[10px] text-overlay-1 font-mono tabular-nums">
							<span>{formatTime(playerStore.elapsedTime)}</span>
							<span class="uppercase tracking-wider text-red-400/70">Live</span>
						</div>
					{:else}
						<!-- Seekable progress -->
						<button
							class="w-full h-1.5 bg-surface-1/30 rounded-full cursor-pointer overflow-hidden group/progress hover:h-2.5 transition-all duration-200"
							onclick={handleSeek}
							aria-label="Seek"
						>
							<div
								class="h-full rounded-full dynamic-progress transition-all duration-150"
								style="width: {playerStore.progress}%"
							></div>
						</button>
						<div class="flex justify-between text-[10px] text-overlay-1 font-mono tabular-nums">
							<span>{formatTime(playerStore.elapsedTime)}</span>
							<span>{formatTime(playerStore.status.duration)}</span>
						</div>
					{/if}
				</div>
			{/if}

			<!-- Controls row -->
			<div class="flex items-center justify-between mt-1">
				<!-- Playback controls -->
				<div class="flex items-center gap-1">
					<button
						class="p-2 rounded-full text-overlay-1 hover:text-text hover:bg-white/[0.06] transition-all duration-200 active:scale-90"
						onclick={() => playerStore.previous()}
						aria-label="Previous"
					>
						<SkipBack size={18} fill="currentColor" />
					</button>

					<button
						class="p-3 rounded-full transition-all duration-200 active:scale-90 shadow-lg
							   hover:shadow-xl hover:brightness-110"
						style="background-color: var(--dynamic-accent); color: var(--color-crust);"
						onclick={() => playerStore.togglePlayPause()}
						aria-label={playerStore.isPlaying ? 'Pause' : 'Play'}
					>
						{#if playerStore.isPlaying}
							<Pause size={20} fill="currentColor" />
						{:else}
							<Play size={20} fill="currentColor" class="ml-0.5" />
						{/if}
					</button>

					<button
						class="p-2 rounded-full text-overlay-1 hover:text-text hover:bg-white/[0.06] transition-all duration-200 active:scale-90"
						onclick={() => playerStore.next()}
						aria-label="Next"
					>
						<SkipForward size={18} fill="currentColor" />
					</button>
				</div>

				<!-- Volume control -->
				<div class="flex items-center gap-2">
					<button
						class="p-1.5 rounded-full text-overlay-1 hover:text-text hover:bg-white/[0.06] transition-colors"
						onclick={() => playerStore.toggleMute()}
						aria-label={playerStore.status.muted ? 'Unmute' : 'Mute'}
					>
						{#if playerStore.status.muted || playerStore.status.volume === 0}
							<VolumeX size={16} />
						{:else}
							<Volume2 size={16} />
						{/if}
					</button>

					<div class="flex items-center gap-2">
						<input
							type="range"
							min="0"
							max="100"
							value={playerStore.status.volume}
							oninput={handleVolumeInput}
							onchange={handleVolumeChange}
							onmousedown={handleVolumeStart}
							ontouchstart={handleVolumeStart}
							class="w-20 h-1 bg-surface-1/40 rounded-full appearance-none cursor-pointer
								   [&::-webkit-slider-thumb]:appearance-none
								   [&::-webkit-slider-thumb]:w-3
								   [&::-webkit-slider-thumb]:h-3
								   [&::-webkit-slider-thumb]:rounded-full
								   [&::-webkit-slider-thumb]:shadow-sm
								   [&::-webkit-slider-thumb]:transition-transform
								   [&::-webkit-slider-thumb]:duration-150
								   [&::-webkit-slider-thumb]:hover:scale-125
								   [&::-moz-range-thumb]:w-3
								   [&::-moz-range-thumb]:h-3
								   [&::-moz-range-thumb]:rounded-full
								   [&::-moz-range-thumb]:border-none"
							aria-label="Volume"
						/>
						<span class="text-[10px] text-overlay-1 font-mono tabular-nums w-7 text-right">
							{displayVolume}
						</span>
					</div>
				</div>
			</div>
		</div>
	</div>
</div>

<style>
	@keyframes live-bar-pulse {
		0%, 100% { opacity: 0.3; }
		50% { opacity: 0.6; }
	}
	:global(.animate-live-bar) {
		animation: live-bar-pulse 2.5s ease-in-out infinite;
	}
</style>
