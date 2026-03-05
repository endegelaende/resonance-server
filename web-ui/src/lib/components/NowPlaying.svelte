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
		Radio,
		Disc3
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

	const hasTrack = $derived(Boolean(playerStore.currentTrack));

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

	let displayVolume = $derived(isDraggingVolume ? previewVolume : playerStore.status.volume);
</script>

<div class="now-playing-root">

	<!-- ── Ambient background: blurred cover art ── -->
	{#if playerStore.currentTrack?.coverArt}
		<div class="ambient-bg">
			<img
				src={playerStore.currentTrack.coverArt}
				alt=""
				class="ambient-img"
				aria-hidden="true"
			/>
			<div class="ambient-overlay"></div>
		</div>
	{/if}

	<!-- ── Content ── -->
	<div class="np-content">

		{#if hasTrack}
			<!-- ── Has a track: show art + info + controls ── -->

			<!-- Album Art -->
			<div class="art-wrapper">
				{#if playerStore.currentTrack?.coverArt}
					<div
						class="art-glow"
						style="background-color: var(--dynamic-accent);"
					></div>
				{/if}
				<div class="art-container">
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

			<!-- Info + Progress + Controls -->
			<div class="info-section">

				<!-- Track metadata -->
				<div class="meta">
					{#if isRadio || isLive}
						<div class="meta-title-row">
							<h2 class="meta-title">{stationName}</h2>
							{#if isLive}
								<span class="live-badge">
									<Radio size={10} />
									Live
								</span>
							{/if}
						</div>

						{#if hasIcyParsed}
							<p class="meta-artist">{icyArtist}</p>
							<p class="meta-album">{icyTitle}</p>
						{:else if hasIcyData}
							<p class="meta-artist">{icyNowPlaying}</p>
						{:else}
							<p class="meta-album italic">Listening…</p>
						{/if}

						<div class="mt-1.5">
							<QualityBadge
								format={getRadioFormat(playerStore.currentTrack?.contentType)}
								bitrate={playerStore.currentTrack?.bitrate}
							/>
						</div>
					{:else}
						<h2 class="meta-title">{playerStore.currentTrack?.title}</h2>
						<p class="meta-artist">{playerStore.currentTrack?.artist}</p>
						<p class="meta-album">{playerStore.currentTrack?.album}</p>

						{#if playerStore.currentTrack?.path}
							<div class="mt-1.5">
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
				</div>

				<!-- Progress bar -->
				<div class="progress-section">
					{#if isLive}
						<div class="progress-track">
							<div class="progress-fill animate-live-bar" style="width: 100%; opacity: 0.5;"></div>
						</div>
						<div class="progress-times">
							<span>{formatTime(playerStore.elapsedTime)}</span>
							<span class="uppercase tracking-wider text-red-400/70">Live</span>
						</div>
					{:else}
						<button
							class="progress-track progress-seekable"
							onclick={handleSeek}
							aria-label="Seek"
						>
							<div
								class="progress-fill"
								style="width: {playerStore.progress}%"
							></div>
						</button>
						<div class="progress-times">
							<span>{formatTime(playerStore.elapsedTime)}</span>
							<span>{formatTime(playerStore.status.duration)}</span>
						</div>
					{/if}
				</div>

				<!-- Controls -->
				<div class="controls-row">
					<!-- Playback -->
					<div class="playback-controls">
						<button
							class="ctrl-btn"
							onclick={() => playerStore.previous()}
							aria-label="Previous"
						>
							<SkipBack size={18} fill="currentColor" />
						</button>

						<button
							class="play-btn"
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
							class="ctrl-btn"
							onclick={() => playerStore.next()}
							aria-label="Next"
						>
							<SkipForward size={18} fill="currentColor" />
						</button>
					</div>

					<!-- Volume -->
					<div class="volume-controls">
						<button
							class="vol-btn"
							onclick={() => playerStore.toggleMute()}
							aria-label={playerStore.status.muted ? 'Unmute' : 'Mute'}
						>
							{#if playerStore.status.muted || playerStore.status.volume === 0}
								<VolumeX size={16} />
							{:else}
								<Volume2 size={16} />
							{/if}
						</button>

						<input
							type="range"
							min="0"
							max="100"
							value={playerStore.status.volume}
							oninput={handleVolumeInput}
							onchange={handleVolumeChange}
							onmousedown={handleVolumeStart}
							ontouchstart={handleVolumeStart}
							class="vol-slider"
							aria-label="Volume"
						/>
						<span class="vol-label">{displayVolume}</span>
					</div>
				</div>
			</div>

		{:else}
			<!-- ── Empty state: no track playing ── -->
			<div class="empty-state">
				<div class="empty-icon">
					<Disc3 size={28} />
				</div>
				<div class="empty-text">
					<p class="empty-title">No track playing</p>
					<p class="empty-sub">Select a track to start</p>
				</div>

				<!-- Still show controls even when empty -->
				<div class="playback-controls" style="margin-left: auto;">
					<button
						class="ctrl-btn"
						onclick={() => playerStore.previous()}
						aria-label="Previous"
					>
						<SkipBack size={18} fill="currentColor" />
					</button>
					<button
						class="play-btn"
						style="background-color: var(--dynamic-accent); color: var(--color-crust);"
						onclick={() => playerStore.togglePlayPause()}
						aria-label="Play"
					>
						<Play size={20} fill="currentColor" class="ml-0.5" />
					</button>
					<button
						class="ctrl-btn"
						onclick={() => playerStore.next()}
						aria-label="Next"
					>
						<SkipForward size={18} fill="currentColor" />
					</button>
				</div>
			</div>
		{/if}
	</div>
</div>

<style>
	/* ── Root ── */
	.now-playing-root {
		position: relative;
		width: 100%;
		height: 100%;
		overflow: hidden;
	}

	/* ── Ambient blurred background ── */
	.ambient-bg {
		position: absolute;
		inset: 0;
		z-index: 0;
		overflow: hidden;
	}

	.ambient-img {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
		transform: scale(1.3);
		filter: blur(80px) saturate(1.4);
		opacity: 0.2;
	}

	.ambient-overlay {
		position: absolute;
		inset: 0;
		background: linear-gradient(
			to top,
			var(--color-base) 0%,
			color-mix(in srgb, var(--color-base) 80%, transparent) 50%,
			color-mix(in srgb, var(--color-base) 40%, transparent) 100%
		);
	}

	/* ── Content layout ── */
	.np-content {
		position: relative;
		z-index: 1;
		display: flex;
		align-items: center;
		gap: 1.5rem;
		height: 100%;
		padding: 0.75rem 1.25rem;
		box-sizing: border-box;
	}

	/* ── Album art ── */
	.art-wrapper {
		position: relative;
		flex-shrink: 0;
		align-self: center;
		/* Art fills the container height minus padding, capped at 200px */
		width: min(calc(100% - 1rem), 200px);
		height: min(calc(100% - 1rem), 200px);
		aspect-ratio: 1;
	}

	/* Use container-query-like approach: let the parent height control this */
	@container (max-height: 200px) {
		.art-wrapper {
			width: 80px;
			height: 80px;
		}
	}

	/* Simpler fallback: use the height of the parent */
	.art-wrapper {
		width: auto;
		height: calc(100% - 1.5rem);
		max-height: 200px;
		max-width: 200px;
		aspect-ratio: 1;
	}

	.art-glow {
		position: absolute;
		inset: -8px;
		border-radius: 1rem;
		opacity: 0.3;
		filter: blur(20px);
		transition: opacity 0.7s ease;
		pointer-events: none;
	}

	.art-wrapper:hover .art-glow {
		opacity: 0.45;
	}

	.art-container {
		position: relative;
		width: 100%;
		height: 100%;
		border-radius: 0.625rem;
		overflow: hidden;
		box-shadow:
			0 8px 30px -8px rgba(0, 0, 0, 0.4),
			0 4px 15px -4px rgba(var(--dynamic-accent-rgb, 224, 159, 90), 0.12);
		ring: 1px solid rgba(255, 255, 255, 0.05);
	}

	/* ── Info section ── */
	.info-section {
		flex: 1;
		display: flex;
		flex-direction: column;
		justify-content: center;
		gap: 0.5rem;
		min-width: 0;
		overflow: hidden;
	}

	/* ── Metadata ── */
	.meta {
		min-width: 0;
		overflow: hidden;
	}

	.meta-title-row {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-bottom: 0.125rem;
	}

	.meta-title {
		font-size: 1rem;
		font-weight: 600;
		color: var(--color-text);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		line-height: 1.3;
	}

	.meta-artist {
		font-size: 0.8125rem;
		color: var(--color-subtext-0);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		margin-top: 0.125rem;
	}

	.meta-album {
		font-size: 0.6875rem;
		color: var(--color-overlay-1);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		margin-top: 0.125rem;
	}

	.live-badge {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		padding: 0.125rem 0.5rem;
		border-radius: 9999px;
		font-size: 0.625rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		background: rgba(239, 68, 68, 0.75);
		color: white;
		flex-shrink: 0;
	}

	/* ── Progress ── */
	.progress-section {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	.progress-track {
		width: 100%;
		height: 4px;
		background: var(--color-surface-1);
		opacity: 0.3;
		border-radius: 9999px;
		overflow: hidden;
		border: none;
		padding: 0;
	}

	.progress-seekable {
		cursor: pointer;
		transition: height 0.2s ease;
	}

	.progress-seekable:hover {
		height: 8px;
	}

	.progress-fill {
		height: 100%;
		border-radius: 9999px;
		background: linear-gradient(
			90deg,
			var(--dynamic-accent, var(--color-accent)) 0%,
			var(--dynamic-accent-light, var(--color-accent-hover)) 100%
		);
		transition: width 0.15s ease, background var(--color-transition);
	}

	.progress-times {
		display: flex;
		justify-content: space-between;
		font-size: 0.625rem;
		color: var(--color-overlay-1);
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
	}

	/* ── Controls ── */
	.controls-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-top: 0.125rem;
	}

	.playback-controls {
		display: flex;
		align-items: center;
		gap: 0.25rem;
	}

	.ctrl-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 0.5rem;
		border-radius: 9999px;
		border: none;
		background: none;
		color: var(--color-overlay-1);
		cursor: pointer;
		transition: all 0.15s ease;
	}

	.ctrl-btn:hover {
		color: var(--color-text);
		background: rgba(255, 255, 255, 0.06);
	}

	.ctrl-btn:active {
		transform: scale(0.9);
	}

	.play-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 0.625rem;
		border-radius: 9999px;
		border: none;
		cursor: pointer;
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
		transition: all 0.2s ease;
	}

	.play-btn:hover {
		filter: brightness(1.1);
		box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4);
	}

	.play-btn:active {
		transform: scale(0.92);
	}

	/* ── Volume ── */
	.volume-controls {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.vol-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 0.375rem;
		border-radius: 9999px;
		border: none;
		background: none;
		color: var(--color-overlay-1);
		cursor: pointer;
		transition: color 0.15s ease;
	}

	.vol-btn:hover {
		color: var(--color-text);
	}

	.vol-slider {
		width: 5rem;
		height: 3px;
		background: var(--color-surface-1);
		opacity: 0.4;
		border-radius: 9999px;
		appearance: none;
		-webkit-appearance: none;
		cursor: pointer;
	}

	.vol-slider::-webkit-slider-thumb {
		-webkit-appearance: none;
		appearance: none;
		width: 12px;
		height: 12px;
		border-radius: 50%;
		background: var(--dynamic-accent, var(--color-accent));
		box-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
		transition: transform 0.15s ease;
	}

	.vol-slider::-webkit-slider-thumb:hover {
		transform: scale(1.25);
	}

	.vol-slider::-moz-range-thumb {
		width: 12px;
		height: 12px;
		border-radius: 50%;
		border: none;
		background: var(--dynamic-accent, var(--color-accent));
	}

	.vol-label {
		font-size: 0.625rem;
		color: var(--color-overlay-1);
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
		width: 1.75rem;
		text-align: right;
	}

	/* ── Empty state ── */
	.empty-state {
		display: flex;
		align-items: center;
		gap: 1rem;
		width: 100%;
		padding: 0 0.5rem;
	}

	.empty-icon {
		flex-shrink: 0;
		width: 48px;
		height: 48px;
		display: flex;
		align-items: center;
		justify-content: center;
		border-radius: 0.75rem;
		background: var(--color-surface-0);
		opacity: 0.5;
		color: var(--color-overlay-0);
	}

	.empty-text {
		min-width: 0;
	}

	.empty-title {
		font-size: 0.875rem;
		font-weight: 500;
		color: var(--color-overlay-1);
	}

	.empty-sub {
		font-size: 0.6875rem;
		color: var(--color-overlay-0);
		margin-top: 0.125rem;
	}

	/* ── Live bar animation ── */
	@keyframes live-bar-pulse {
		0%, 100% { opacity: 0.3; }
		50% { opacity: 0.6; }
	}

	:global(.animate-live-bar) {
		animation: live-bar-pulse 2.5s ease-in-out infinite;
	}
</style>
