<script lang="ts">
  import { api, type AlarmEntry, type AlarmUpdateInput } from "$lib/api";
  import { playerStore } from "$lib/stores/player.svelte";
  import {
    AlarmClock,
    AlertCircle,
    Plus,
    RefreshCw,
    Save,
    Trash2,
  } from "lucide-svelte";

  const DAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;
  const DEFAULT_NEW_DOW = [true, true, true, true, true, true, true];

  interface AlarmDraft {
    timeText: string;
    volumeText: string;
    enabled: boolean;
    repeat: boolean;
    dow: boolean[];
  }

  let alarms = $state<AlarmEntry[]>([]);
  let drafts = $state<Record<string, AlarmDraft>>({});
  let isLoading = $state(false);
  let error = $state("");
  let busy = $state<string | null>(null);

  let defaultVolume = $state("50");
  let newTime = $state("07:00");
  let newVolume = $state("50");
  let newEnabled = $state(true);
  let newRepeat = $state(true);
  let newDow = $state<boolean[]>([...DEFAULT_NEW_DOW]);

  let requestToken = 0;

  function clamp(value: number, min: number, max: number): number {
    return Math.max(min, Math.min(max, value));
  }

  function parseVolume(value: string, fallback: number): number {
    const parsed = Number.parseInt(value.trim(), 10);
    if (!Number.isFinite(parsed)) {
      return clamp(fallback, 0, 100);
    }
    return clamp(parsed, 0, 100);
  }

  function timeToSeconds(timeText: string): number | null {
    const match = timeText.trim().match(/^(\d{1,2}):(\d{2})$/);
    if (!match) {
      return null;
    }
    const hour = Number.parseInt(match[1], 10);
    const minute = Number.parseInt(match[2], 10);
    if (!Number.isFinite(hour) || !Number.isFinite(minute)) {
      return null;
    }
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) {
      return null;
    }
    return hour * 3600 + minute * 60;
  }

  function secondsToTime(seconds: number): string {
    const safe = Math.max(0, Math.floor(seconds));
    const hour = Math.floor((safe % 86400) / 3600);
    const minute = Math.floor((safe % 3600) / 60);
    return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  }

  function normalizeDow(dow: number[]): number[] {
    return Array.from(
      new Set(dow.filter((day) => Number.isFinite(day) && day >= 0 && day <= 6)),
    ).sort((a, b) => a - b);
  }

  function dowToSelection(dow: number[]): boolean[] {
    const selection = [false, false, false, false, false, false, false];
    for (const day of normalizeDow(dow)) {
      selection[day] = true;
    }
    return selection;
  }

  function selectionToDow(selection: boolean[]): number[] {
    const days: number[] = [];
    for (let day = 0; day < 7; day += 1) {
      if (selection[day]) {
        days.push(day);
      }
    }
    return days;
  }

  function buildDraft(alarm: AlarmEntry): AlarmDraft {
    return {
      timeText: secondsToTime(alarm.time),
      volumeText: String(clamp(alarm.volume, 0, 100)),
      enabled: alarm.enabled,
      repeat: alarm.repeat,
      dow: dowToSelection(alarm.dow),
    };
  }

  function setDraft(alarmId: string, patch: Partial<AlarmDraft>): void {
    const current = drafts[alarmId];
    if (!current) return;
    drafts = {
      ...drafts,
      [alarmId]: {
        ...current,
        ...patch,
      },
    };
  }

  function setDraftDay(alarmId: string, day: number, enabled: boolean): void {
    const current = drafts[alarmId];
    if (!current || day < 0 || day > 6) return;
    const nextDow = [...current.dow];
    nextDow[day] = enabled;
    setDraft(alarmId, { dow: nextDow });
  }

  function setNewDay(day: number, enabled: boolean): void {
    if (day < 0 || day > 6) return;
    const nextDow = [...newDow];
    nextDow[day] = enabled;
    newDow = nextDow;
  }

  function draftFor(alarmId: string): AlarmDraft | null {
    return drafts[alarmId] ?? null;
  }

  async function loadAlarms(playerId: string): Promise<void> {
    const token = ++requestToken;
    isLoading = true;
    error = "";

    try {
      const loaded = await api.getAlarms(playerId);
      if (token !== requestToken) return;

      alarms = loaded;
      drafts = Object.fromEntries(loaded.map((alarm) => [alarm.id, buildDraft(alarm)]));

      if (loaded.length > 0) {
        defaultVolume = String(clamp(loaded[0].volume, 0, 100));
      }
    } catch (err) {
      if (token !== requestToken) return;
      error = (err as Error).message;
    } finally {
      if (token === requestToken) {
        isLoading = false;
      }
    }
  }

  async function refreshAlarms(): Promise<void> {
    const playerId = playerStore.selectedPlayerId;
    if (!playerId) return;
    await loadAlarms(playerId);
  }

  async function addAlarm(): Promise<void> {
    const playerId = playerStore.selectedPlayerId;
    if (!playerId || busy !== null) return;

    const timeSeconds = timeToSeconds(newTime);
    if (timeSeconds === null) {
      error = "Invalid alarm time. Use HH:MM.";
      return;
    }

    const dow = selectionToDow(newDow);
    if (newRepeat && dow.length === 0) {
      error = "Select at least one day for a repeating alarm.";
      return;
    }

    const update: AlarmUpdateInput & { timeSeconds: number } = {
      timeSeconds,
      volume: parseVolume(newVolume, parseVolume(defaultVolume, 50)),
      enabled: newEnabled,
      repeat: newRepeat,
      dow,
    };

    busy = "add";
    error = "";

    try {
      await api.addAlarm(playerId, update);
      await loadAlarms(playerId);
      newTime = "07:00";
      newVolume = defaultVolume;
      newEnabled = true;
      newRepeat = true;
      newDow = [...DEFAULT_NEW_DOW];
    } catch (err) {
      error = (err as Error).message;
    } finally {
      busy = null;
    }
  }

  async function saveAlarm(alarmId: string): Promise<void> {
    const playerId = playerStore.selectedPlayerId;
    const draft = draftFor(alarmId);
    if (!playerId || !draft || busy !== null) return;

    const timeSeconds = timeToSeconds(draft.timeText);
    if (timeSeconds === null) {
      error = "Invalid alarm time. Use HH:MM.";
      return;
    }

    const dow = selectionToDow(draft.dow);
    if (draft.repeat && dow.length === 0) {
      error = "Select at least one day for a repeating alarm.";
      return;
    }

    busy = `save:${alarmId}`;
    error = "";

    try {
      await api.updateAlarm(playerId, alarmId, {
        timeSeconds,
        volume: parseVolume(draft.volumeText, 50),
        enabled: draft.enabled,
        repeat: draft.repeat,
        dow,
      });
      await loadAlarms(playerId);
    } catch (err) {
      error = (err as Error).message;
    } finally {
      busy = null;
    }
  }

  async function deleteAlarm(alarmId: string): Promise<void> {
    const playerId = playerStore.selectedPlayerId;
    if (!playerId || busy !== null) return;

    busy = `delete:${alarmId}`;
    error = "";

    try {
      await api.deleteAlarm(playerId, alarmId);
      await loadAlarms(playerId);
    } catch (err) {
      error = (err as Error).message;
    } finally {
      busy = null;
    }
  }

  async function setAllEnabled(enabled: boolean): Promise<void> {
    const playerId = playerStore.selectedPlayerId;
    if (!playerId || busy !== null) return;

    busy = enabled ? "enableall" : "disableall";
    error = "";

    try {
      if (enabled) {
        await api.enableAllAlarms(playerId);
      } else {
        await api.disableAllAlarms(playerId);
      }
      await loadAlarms(playerId);
    } catch (err) {
      error = (err as Error).message;
    } finally {
      busy = null;
    }
  }

  async function saveDefaultVolume(): Promise<void> {
    const playerId = playerStore.selectedPlayerId;
    if (!playerId || busy !== null) return;

    busy = "defaultvolume";
    error = "";

    try {
      const applied = await api.setDefaultAlarmVolume(
        playerId,
        parseVolume(defaultVolume, 50),
      );
      defaultVolume = String(applied);
      newVolume = String(applied);
    } catch (err) {
      error = (err as Error).message;
    } finally {
      busy = null;
    }
  }

  $effect(() => {
    const playerId = playerStore.selectedPlayerId;

    if (!playerId) {
      alarms = [];
      drafts = {};
      error = "";
      defaultVolume = "50";
      return;
    }

    loadAlarms(playerId);
  });
</script>

<section class="rounded-xl border border-border bg-surface-0 p-5 space-y-4">
  <div class="flex items-center justify-between gap-3">
    <div>
      <h3 class="text-lg font-semibold text-text">Alarms</h3>
      <p class="text-sm text-overlay-1">Configure LMS-compatible alarm schedules.</p>
    </div>

    <div class="flex flex-wrap items-center gap-2">
      <button
        class="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-border hover:bg-surface-1 text-xs text-overlay-1 hover:text-text transition-colors disabled:opacity-60"
        onclick={refreshAlarms}
        disabled={!playerStore.selectedPlayerId || isLoading || busy !== null}
      >
        <RefreshCw size={13} class={isLoading ? "animate-spin" : ""} />
        Refresh
      </button>
      <button
        class="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-border hover:bg-surface-1 text-xs text-overlay-1 hover:text-text transition-colors disabled:opacity-60"
        onclick={() => setAllEnabled(true)}
        disabled={!playerStore.selectedPlayerId || isLoading || busy !== null || alarms.length === 0}
      >
        {busy === "enableall" ? "Enabling..." : "Enable all"}
      </button>
      <button
        class="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-border hover:bg-surface-1 text-xs text-overlay-1 hover:text-text transition-colors disabled:opacity-60"
        onclick={() => setAllEnabled(false)}
        disabled={!playerStore.selectedPlayerId || isLoading || busy !== null || alarms.length === 0}
      >
        {busy === "disableall" ? "Disabling..." : "Disable all"}
      </button>
    </div>
  </div>

  {#if error}
    <div class="rounded-lg border border-error/50 bg-error/10 px-3 py-2 text-sm text-error flex items-center gap-2">
      <AlertCircle size={14} />
      {error}
    </div>
  {/if}

  {#if !playerStore.selectedPlayerId}
    <p class="text-sm text-overlay-1">Select a player to manage alarms.</p>
  {:else}
    <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div class="rounded-lg border border-border bg-base p-4 space-y-3">
        <h4 class="text-sm font-medium text-text">Default alarm volume</h4>
        <div class="flex items-center gap-2">
          <input
            class="w-28 rounded-lg bg-surface-0 border border-border px-3 py-2 text-text"
            type="number"
            min="0"
            max="100"
            bind:value={defaultVolume}
            disabled={isLoading || busy !== null}
          />
          <button
            class="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-1 hover:bg-surface-2 text-text text-sm transition-colors disabled:opacity-60"
            onclick={saveDefaultVolume}
            disabled={isLoading || busy !== null}
          >
            <Save size={14} />
            {busy === "defaultvolume" ? "Saving..." : "Set default"}
          </button>
        </div>
        <p class="text-xs text-overlay-1">Applied to newly created alarms.</p>
      </div>

      <div class="rounded-lg border border-border bg-base p-4 space-y-3">
        <h4 class="text-sm font-medium text-text">Add alarm</h4>

        <div class="grid grid-cols-2 gap-3">
          <label class="space-y-1">
            <span class="text-xs text-overlay-1">Time</span>
            <input
              class="w-full rounded-lg bg-surface-0 border border-border px-3 py-2 text-text"
              type="time"
              bind:value={newTime}
              disabled={isLoading || busy !== null}
            />
          </label>

          <label class="space-y-1">
            <span class="text-xs text-overlay-1">Volume</span>
            <input
              class="w-full rounded-lg bg-surface-0 border border-border px-3 py-2 text-text"
              type="number"
              min="0"
              max="100"
              bind:value={newVolume}
              disabled={isLoading || busy !== null}
            />
          </label>
        </div>

        <div class="flex flex-wrap gap-4">
          <label class="inline-flex items-center gap-2 text-xs text-text">
            <input
              type="checkbox"
              class="rounded border-border"
              checked={newEnabled}
              onchange={(event) => newEnabled = (event.currentTarget as HTMLInputElement).checked}
              disabled={isLoading || busy !== null}
            />
            Enabled
          </label>
          <label class="inline-flex items-center gap-2 text-xs text-text">
            <input
              type="checkbox"
              class="rounded border-border"
              checked={newRepeat}
              onchange={(event) => newRepeat = (event.currentTarget as HTMLInputElement).checked}
              disabled={isLoading || busy !== null}
            />
            Repeat
          </label>
        </div>

        <div class="flex flex-wrap gap-1.5">
          {#each DAY_LABELS as dayLabel, dayIndex}
            <button
              class="px-2 py-1 text-xs rounded-md border transition-colors {newDow[dayIndex]
                ? 'bg-accent text-mantle border-accent'
                : 'bg-surface-0 border-border text-overlay-1 hover:text-text hover:bg-surface-1'}"
              onclick={() => setNewDay(dayIndex, !newDow[dayIndex])}
              disabled={isLoading || busy !== null}
            >
              {dayLabel}
            </button>
          {/each}
        </div>

        <button
          class="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-accent text-mantle hover:bg-accent-hover transition-colors disabled:opacity-60"
          onclick={addAlarm}
          disabled={isLoading || busy !== null}
        >
          <Plus size={14} />
          {busy === "add" ? "Adding..." : "Add alarm"}
        </button>
      </div>
    </div>

    <div class="space-y-3">
      <div class="flex items-center gap-2 text-sm text-overlay-1">
        <AlarmClock size={15} />
        Existing alarms ({alarms.length})
      </div>

      {#if isLoading && alarms.length === 0}
        <p class="text-sm text-overlay-1">Loading alarms...</p>
      {:else if alarms.length === 0}
        <p class="text-sm text-overlay-1">No alarms configured.</p>
      {:else}
        {#each alarms as alarm}
          {@const draft = draftFor(alarm.id)}
          {#if draft}
            <div class="rounded-lg border border-border bg-base p-4 space-y-3">
              <div class="flex items-center justify-between gap-3">
                <div>
                  <p class="text-sm font-medium text-text">Alarm {alarm.id.slice(0, 8)}</p>
                  <p class="text-xs text-overlay-1">Current time {secondsToTime(alarm.time)}</p>
                </div>
                <div class="flex items-center gap-2">
                  <button
                    class="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md bg-surface-1 hover:bg-surface-2 text-text text-xs transition-colors disabled:opacity-60"
                    onclick={() => saveAlarm(alarm.id)}
                    disabled={isLoading || busy !== null}
                  >
                    <Save size={13} />
                    {busy === `save:${alarm.id}` ? "Saving..." : "Save"}
                  </button>
                  <button
                    class="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md bg-error/80 hover:bg-error text-white text-xs transition-colors disabled:opacity-60"
                    onclick={() => deleteAlarm(alarm.id)}
                    disabled={isLoading || busy !== null}
                  >
                    <Trash2 size={13} />
                    {busy === `delete:${alarm.id}` ? "Deleting..." : "Delete"}
                  </button>
                </div>
              </div>

              <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
                <label class="space-y-1">
                  <span class="text-xs text-overlay-1">Time</span>
                  <input
                    class="w-full rounded-lg bg-surface-0 border border-border px-3 py-2 text-text"
                    type="time"
                    value={draft.timeText}
                    onchange={(event) =>
                      setDraft(alarm.id, {
                        timeText: (event.currentTarget as HTMLInputElement).value,
                      })}
                    disabled={isLoading || busy !== null}
                  />
                </label>

                <label class="space-y-1">
                  <span class="text-xs text-overlay-1">Volume</span>
                  <input
                    class="w-full rounded-lg bg-surface-0 border border-border px-3 py-2 text-text"
                    type="number"
                    min="0"
                    max="100"
                    value={draft.volumeText}
                    onchange={(event) =>
                      setDraft(alarm.id, {
                        volumeText: (event.currentTarget as HTMLInputElement).value,
                      })}
                    disabled={isLoading || busy !== null}
                  />
                </label>

                <label class="inline-flex items-center gap-2 text-xs text-text pt-6">
                  <input
                    type="checkbox"
                    class="rounded border-border"
                    checked={draft.enabled}
                    onchange={(event) =>
                      setDraft(alarm.id, {
                        enabled: (event.currentTarget as HTMLInputElement).checked,
                      })}
                    disabled={isLoading || busy !== null}
                  />
                  Enabled
                </label>

                <label class="inline-flex items-center gap-2 text-xs text-text pt-6">
                  <input
                    type="checkbox"
                    class="rounded border-border"
                    checked={draft.repeat}
                    onchange={(event) =>
                      setDraft(alarm.id, {
                        repeat: (event.currentTarget as HTMLInputElement).checked,
                      })}
                    disabled={isLoading || busy !== null}
                  />
                  Repeat
                </label>
              </div>

              <div class="flex flex-wrap gap-1.5">
                {#each DAY_LABELS as dayLabel, dayIndex}
                  <button
                    class="px-2 py-1 text-xs rounded-md border transition-colors {draft.dow[dayIndex]
                      ? 'bg-accent text-mantle border-accent'
                      : 'bg-surface-0 border-border text-overlay-1 hover:text-text hover:bg-surface-1'}"
                    onclick={() => setDraftDay(alarm.id, dayIndex, !draft.dow[dayIndex])}
                    disabled={isLoading || busy !== null}
                  >
                    {dayLabel}
                  </button>
                {/each}
              </div>
            </div>
          {/if}
        {/each}
      {/if}
    </div>
  {/if}
</section>

