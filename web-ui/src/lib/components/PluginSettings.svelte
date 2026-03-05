<!--
  PluginSettings.svelte — "Settings" tab for the Plugins page.

  Renders a type-aware settings form for the currently selected plugin,
  with Save and Reset Defaults actions. Supports string, int, float,
  bool, and select field types with validation constraints.
-->
<script lang="ts">
  import type { PluginInfo, PluginSettingDefinition } from "$lib/api";
  import { Save, RotateCcw } from "lucide-svelte";

  interface Props {
    selectedPlugin: string | null;
    selectedPluginInfo: PluginInfo | null;
    definitions: PluginSettingDefinition[];
    values: Record<string, unknown>;
    saving: boolean;
    onSave: () => void;
    onResetDefaults: () => void;
    onValuesChange: (values: Record<string, unknown>) => void;
  }

  let {
    selectedPlugin,
    selectedPluginInfo,
    definitions,
    values,
    saving,
    onSave,
    onResetDefaults,
    onValuesChange,
  }: Props = $props();

  function parseInputValue(definition: PluginSettingDefinition, raw: unknown): unknown {
    if (definition.type === "int") {
      const n = Number.parseInt(String(raw), 10);
      return Number.isNaN(n) ? 0 : n;
    }
    if (definition.type === "float") {
      const n = Number.parseFloat(String(raw));
      return Number.isNaN(n) ? 0 : n;
    }
    if (definition.type === "bool") {
      return Boolean(raw);
    }
    return String(raw ?? "");
  }

  function updateValue(key: string, definition: PluginSettingDefinition, raw: unknown) {
    onValuesChange({ ...values, [key]: parseInputValue(definition, raw) });
  }
</script>

{#if !selectedPlugin}
  <div class="p-8 rounded-xl bg-surface-0 text-overlay-1 text-center">
    Select a plugin with settings from the Installed tab.
  </div>
{:else}
  <div class="p-4 rounded-xl bg-surface-0 border border-border space-y-4">
    <!-- Header with plugin name + save/reset actions -->
    <div class="flex items-center justify-between gap-3">
      <div>
        <h3 class="text-text font-semibold">{selectedPlugin}</h3>
        <p class="text-xs text-overlay-1">
          {selectedPluginInfo?.description || "Plugin settings"}
        </p>
      </div>
      <div class="flex gap-2">
        <button
          class="px-3 py-2 rounded-lg bg-surface-1 hover:bg-surface-2 text-text transition-colors flex items-center gap-2"
          onclick={onResetDefaults}
          disabled={saving}
        >
          <RotateCcw size={14} />
          Reset Defaults
        </button>
        <button
          class="px-3 py-2 rounded-lg bg-accent text-mantle font-semibold hover:bg-accent-hover transition-colors disabled:opacity-60 flex items-center gap-2"
          onclick={onSave}
          disabled={saving}
        >
          <Save size={14} />
          {saving ? "Saving..." : "Save"}
        </button>
      </div>
    </div>

    <!-- Settings form -->
    {#if definitions.length === 0}
      <div class="p-6 rounded-lg bg-surface-1 text-overlay-1 text-sm">
        This plugin has no configurable settings.
      </div>
    {:else}
      <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {#each definitions as definition (definition.key)}
          <label class="p-3 rounded-lg bg-surface-1/70 border border-border/50 space-y-2">
            <div class="flex items-center justify-between gap-2">
              <span class="text-sm font-medium text-text">{definition.label}</span>
              {#if definition.restart_required}
                <span class="text-[11px] px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-200">
                  restart
                </span>
              {/if}
            </div>

            {#if definition.type === "bool"}
              <input
                type="checkbox"
                checked={Boolean(values[definition.key])}
                onchange={(event) =>
                  updateValue(
                    definition.key,
                    definition,
                    (event.currentTarget as HTMLInputElement).checked,
                  )}
              />
            {:else if definition.type === "select"}
              <select
                class="w-full px-3 py-2 rounded-md bg-surface-0 border border-border text-sm text-text"
                value={String(values[definition.key] ?? definition.default ?? "")}
                onchange={(event) =>
                  updateValue(
                    definition.key,
                    definition,
                    (event.currentTarget as HTMLSelectElement).value,
                  )}
              >
                {#each definition.options ?? [] as option}
                  <option value={option}>{option}</option>
                {/each}
              </select>
            {:else}
              <input
                class="w-full px-3 py-2 rounded-md bg-surface-0 border border-border text-sm text-text placeholder:text-overlay-1"
                type={definition.secret
                  ? "password"
                  : definition.type === "int" || definition.type === "float"
                    ? "number"
                    : "text"}
                min={definition.min}
                max={definition.max}
                step={definition.type === "float"
                  ? "0.1"
                  : definition.type === "int"
                    ? "1"
                    : undefined}
                value={String(values[definition.key] ?? definition.default ?? "")}
                oninput={(event) =>
                  updateValue(
                    definition.key,
                    definition,
                    (event.currentTarget as HTMLInputElement).value,
                  )}
              />
            {/if}

            {#if definition.description}
              <p class="text-xs text-overlay-1">{definition.description}</p>
            {/if}
          </label>
        {/each}
      </div>
    {/if}
  </div>
{/if}
