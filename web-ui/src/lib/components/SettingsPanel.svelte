<script lang="ts">
  import { playerStore } from "$lib/stores/player.svelte";

  const selectedPlayer = $derived(playerStore.selectedPlayer);
</script>

<div class="p-6 space-y-6">
  <div>
    <h2 class="text-2xl font-semibold text-text">Settings</h2>
    <p class="text-sm text-overlay-1 mt-1">
      Test mode: advanced settings are temporarily disabled.
    </p>
  </div>

  {#if !selectedPlayer}
    <div class="rounded-xl border border-border bg-surface-0 p-6 text-overlay-1">
      Select a player first.
    </div>
  {:else}
    <div class="rounded-xl border border-border bg-surface-0 p-4">
      <p class="text-sm text-overlay-1">Active player</p>
      <p class="text-text font-medium mt-1">{selectedPlayer.name}</p>
      <p class="text-xs text-overlay-0 mt-1">{selectedPlayer.id}</p>
    </div>

  {/if}
</div>
