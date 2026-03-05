import type { Component } from "svelte";
import Heading from "./widgets/Heading.svelte";
import TextBlock from "./widgets/TextBlock.svelte";
import StatusBadge from "./widgets/StatusBadge.svelte";
import KeyValue from "./widgets/KeyValue.svelte";
import DataTable from "./widgets/DataTable.svelte";
import ActionButton from "./widgets/ActionButton.svelte";
import Card from "./widgets/Card.svelte";
import Row from "./widgets/Row.svelte";
import Column from "./widgets/Column.svelte";
import Alert from "./widgets/Alert.svelte";
import ProgressBar from "./widgets/ProgressBar.svelte";
import MarkdownBlock from "./widgets/MarkdownBlock.svelte";
// Phase 2 — Layout
import Tabs from "./widgets/Tabs.svelte";
// Phase 2 — Form widgets
import TextInput from "./widgets/TextInput.svelte";
// Phase 2.5 — Modal
import Modal from "./widgets/Modal.svelte";
// Phase 3 — Textarea
import Textarea from "./widgets/Textarea.svelte";
import NumberInput from "./widgets/NumberInput.svelte";
import Select from "./widgets/Select.svelte";
import Toggle from "./widgets/Toggle.svelte";
import Form from "./widgets/Form.svelte";

export const registry: Record<string, Component<any>> = {
  heading: Heading,
  text: TextBlock,
  status_badge: StatusBadge,
  key_value: KeyValue,
  table: DataTable,
  button: ActionButton,
  card: Card,
  row: Row,
  column: Column,
  alert: Alert,
  progress: ProgressBar,
  markdown: MarkdownBlock,
  // Phase 2 — Layout
  tabs: Tabs,
  // Phase 2 — Form widgets
  text_input: TextInput,
  number_input: NumberInput,
  select: Select,
  toggle: Toggle,
  form: Form,
  // Phase 3 — Textarea
  textarea: Textarea,
  // Phase 2.5 — Modal
  modal: Modal,
};
