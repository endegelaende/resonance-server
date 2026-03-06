<script lang="ts">
  import { getContext } from "svelte";
  import { pluginActions } from "../actions.svelte.js";
  import { toastStore } from "$lib/stores/toast.svelte";

  let {
    columns = [],
    rows = [],
    title = null,
    edit_action = null,
    row_key = "udn",
  }: {
    columns?: Array<{ key: string; label: string; variant?: string }>;
    rows?: Array<Record<string, any>>;
    title?: string | null;
    edit_action?: string | null;
    row_key?: string;
  } = $props();

  const pluginId = getContext<string>("pluginId");

  const colorMap: Record<string, string> = {
    green: "bg-success/20 text-success",
    red: "bg-error/20 text-error",
    yellow: "bg-warning/20 text-warning",
    blue: "bg-accent/20 text-accent",
    gray: "bg-overlay-0/20 text-overlay-1",
  };

  const buttonStyleMap: Record<string, string> = {
    primary: "bg-accent text-crust hover:opacity-80",
    secondary: "bg-surface-1 text-text hover:bg-surface-2",
    danger: "bg-error text-crust hover:opacity-80",
  };

  // Track which cell is currently being edited: "rowIndex-colKey"
  let editingCell = $state<string | null>(null);

  // Temporary edit value while typing
  let editValue = $state("");

  // Track row-click loading state to prevent double-clicks
  let rowClickLoading = $state<number | null>(null);

  function cellId(rowIndex: number, colKey: string): string {
    return `${rowIndex}-${colKey}`;
  }

  function startEdit(rowIndex: number, colKey: string, currentValue: string) {
    editingCell = cellId(rowIndex, colKey);
    editValue = currentValue ?? "";
  }

  async function commitEdit(rowIndex: number, col: { key: string }) {
    const row = rows[rowIndex];
    if (!row) return;

    const originalValue = row[col.key] ?? "";
    const newValue = editValue.trim();

    // Close editor
    editingCell = null;

    // Only dispatch if value actually changed
    if (newValue === String(originalValue)) return;

    if (!edit_action) return;

    const params: Record<string, any> = {
      [row_key]: row[row_key],
      [col.key]: newValue,
    };

    try {
      const result = await pluginActions.dispatch(pluginId, edit_action, params);
      if (result?.message) toastStore.success(result.message);
    } catch (e: any) {
      toastStore.error(`Edit failed: ${e.message}`);
    }
  }

  function cancelEdit() {
    editingCell = null;
    editValue = "";
  }

  function handleEditKeydown(e: KeyboardEvent, rowIndex: number, col: { key: string }) {
    if (e.key === "Enter") {
      e.preventDefault();
      commitEdit(rowIndex, col);
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancelEdit();
    }
  }

  async function handleAction(
    action: string,
    params: Record<string, any> = {},
    confirmMsg?: string,
  ) {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    try {
      const result = await pluginActions.dispatch(pluginId, action, params);
      if (result?.message) toastStore.success(result.message);
    } catch (e: any) {
      toastStore.error(`Action failed: ${e.message}`);
    }
  }

  /**
   * Handle row click — dispatches edit_action with the full row data.
   * Only fires when edit_action is set and the click target is not an
   * action button, editable cell, or interactive element.
   */
  async function handleRowClick(e: MouseEvent, rowIndex: number) {
    if (!edit_action) return;

    // Don't handle if click originated from an interactive element
    const target = e.target as HTMLElement;
    if (target.closest("button, input, a, [role='button']")) return;

    // Prevent double-clicks
    if (rowClickLoading !== null) return;

    const row = rows[rowIndex];
    if (!row) return;

    // Check if row has any column with variant "actions" or "editable" that was clicked
    // If so, let those handlers deal with it instead
    const clickedTd = target.closest("td");
    if (clickedTd) {
      const colIndex = Array.from(clickedTd.parentElement?.children ?? []).indexOf(clickedTd);
      if (colIndex >= 0 && colIndex < columns.length) {
        const col = columns[colIndex];
        if (col.variant === "actions" || col.variant === "editable") return;
      }
    }

    rowClickLoading = rowIndex;
    try {
      const result = await pluginActions.dispatch(pluginId, edit_action, { row });
      if (result?.message) toastStore.success(result.message);
      if (result?.error) toastStore.error(result.error);
    } catch (e: any) {
      toastStore.error(`Action failed: ${e.message}`);
    } finally {
      rowClickLoading = null;
    }
  }

  // Check if any column has variant "editable"
  const hasEditableColumns = $derived(columns.some((c) => c.variant === "editable"));

  // Row is clickable if edit_action is set (for row-click dispatch)
  const isRowClickable = $derived(!!edit_action);
</script>

{#if title}
  <h3
    class="text-sm font-semibold text-subtext-1 uppercase tracking-wider mb-3"
  >
    {title}
  </h3>
{/if}

<div class="overflow-x-auto rounded-lg border border-border">
  <table class="w-full text-sm">
    <thead>
      <tr class="border-b border-border bg-surface-0">
        {#each columns as col}
          <th class="px-4 py-3 text-left text-xs font-semibold text-overlay-1 uppercase tracking-wider">
            {col.label}
          </th>
        {/each}
      </tr>
    </thead>
    <tbody>
      {#each rows as row, i}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <tr
          class="border-b border-border last:border-0 hover:bg-surface-0/50 transition-colors
            {isRowClickable ? 'cursor-pointer' : ''}
            {rowClickLoading === i ? 'opacity-60' : ''}"
          onclick={(e) => handleRowClick(e, i)}
        >
          {#each columns as col}
            <td class="px-4 py-3">
              {#if col.variant === "badge"}
                {@const cell = row[col.key]}
                {#if typeof cell === "object" && cell}
                  <span
                    class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium {colorMap[
                      cell.color
                    ] ?? colorMap.gray}"
                  >
                    {cell.text ?? cell.value ?? ""}
                  </span>
                {:else}
                  <span class="text-text">{cell ?? ""}</span>
                {/if}
              {:else if col.variant === "actions"}
                {@const actions = row[col.key] ?? []}
                <div class="flex gap-2">
                  {#each actions as act}
                    <button
                      class="rounded px-2.5 py-1 text-xs font-medium transition-all {buttonStyleMap[
                        act.style ?? 'secondary'
                      ] ?? buttonStyleMap.secondary}"
                      onclick={() =>
                        handleAction(
                          act.action,
                          act.params ?? {},
                          act.confirm
                            ? `Are you sure you want to ${act.label.toLowerCase()}?`
                            : undefined,
                        )}
                    >
                      {act.label}
                    </button>
                  {/each}
                </div>
              {:else if col.variant === "editable" && edit_action}
                {#if editingCell === cellId(i, col.key)}
                  <!-- svelte-ignore a11y_autofocus -->
                  <input
                    type="text"
                    bind:value={editValue}
                    class="w-full rounded border border-accent bg-surface-0 px-2 py-1 text-sm text-text
                      focus:outline-none focus:ring-2 focus:ring-accent/50"
                    onblur={() => commitEdit(i, col)}
                    onkeydown={(e) => handleEditKeydown(e, i, col)}
                    autofocus
                  />
                {:else}
                  <!-- svelte-ignore a11y_click_events_have_key_events -->
                  <!-- svelte-ignore a11y_no_static_element_interactions -->
                  <span
                    class="group inline-flex items-center gap-1.5 cursor-pointer rounded px-1 -mx-1 py-0.5
                      hover:bg-surface-1 transition-colors"
                    onclick={() => startEdit(i, col.key, row[col.key] ?? "")}
                    title="Click to edit"
                  >
                    <span class="text-text">{row[col.key] ?? ""}</span>
                    <svg
                      class="h-3 w-3 text-overlay-0 opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    >
                      <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                      <path d="m15 5 4 4" />
                    </svg>
                  </span>
                {/if}
              {:else}
                <span class="text-text">{row[col.key] ?? ""}</span>
              {/if}
            </td>
          {/each}
        </tr>
      {/each}
    </tbody>
  </table>
</div>
