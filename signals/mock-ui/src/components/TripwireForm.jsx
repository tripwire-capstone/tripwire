import { useEffect, useId, useState } from "react";
import {
  Accordion,
  ActionIcon,
  Alert,
  Box,
  Button,
  Divider,
  Group,
  NumberInput,
  Radio,
  Select,
  SimpleGrid,
  Slider,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import { FEATURES } from "../api";
import { SeverityBadge } from "./Badges";
import { useStoryData } from "./StoryClock";
import { formatCost, formatTime } from "./format";
import classes from "./TripwireForm.module.css";

const TYPES = [
  { value: "string", label: "String (text)" },
  { value: "number", label: "Number" },
  { value: "boolean", label: "Boolean (yes/no)" },
  { value: "enum", label: "Enum (choices)" },
];
const FILTERS = {
  total_token_count: {
    label: "Total tokens",
    operators: [
      { value: "gt", label: ">" },
      { value: "gte", label: "≥" },
      { value: "lt", label: "<" },
      { value: "lte", label: "≤" },
      { value: "eq", label: "=" },
      { value: "ne", label: "≠" },
    ],
  },
  status: {
    label: "Status",
    operators: [
      { value: "eq", label: "equals" },
      ...(FEATURES.statusNotEquals ? [{ value: "ne", label: "does not equal" }] : []),
    ],
  },
  span_names: {
    label: "Span names",
    operators: [
      { value: "includes", label: "includes" },
      { value: "not_includes", label: "does not include" },
    ],
  },
  tags: {
    label: "Span tags",
    operators: [
      { value: "includes", label: "includes" },
      { value: "not_includes", label: "does not include" },
    ],
  },
};
// The filters this form offers. Status is always supported; the rest wait for the backend (src/api/features.js).
const OFFERED_FILTERS = Object.fromEntries(
  Object.entries(FILTERS).filter(([field]) =>
    field === "status" ||
    (field === "total_token_count" && FEATURES.tokenFilter) ||
    (["span_names", "tags"].includes(field) && FEATURES.spanAndTagFilters),
  ),
);
const listValues = (text) =>
  text
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
const makeField = (field = {}) => ({
  id: crypto.randomUUID(),
  name: "",
  description: "",
  type: "string",
  ...field,
  choices: Array.isArray(field.choices)
    ? field.choices.join(", ")
    : field.choices || "",
});
function Icon({ name }) {
  const paths = {
    "alert-circle": (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v4m0 4h.01" />
      </>
    ),
    brain: (
      <>
        <path d="M12 5a3 3 0 0 0-5-2 4 4 0 0 0-3 6 4 4 0 0 0 0 7 4 4 0 0 0 5 5 3 3 0 0 0 3-3V5Zm0 0a3 3 0 0 1 5-2 4 4 0 0 1 3 6 4 4 0 0 1 0 7 4 4 0 0 1-5 5 3 3 0 0 1-3-3" />
        <path d="M7 8c3 0 3 3 3 3m7-3c-3 0-3 3-3 3M7 16c3 0 3-2 3-2m7 2c-3 0-3-2-3-2" />
      </>
    ),
    "check-circle": (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="m8 12 3 3 5-6" />
      </>
    ),
    frown: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M8 9h.01M16 9h.01M8 16c2-3 6-3 8 0" />
      </>
    ),
    "cloud-off": (
      <>
        <path d="m3 3 18 18M6 7a5 5 0 0 0 0 10h10M10 4a6 6 0 0 1 9 5 4 4 0 0 1 2 7" />
      </>
    ),
    target: (
      <>
        <circle cx="12" cy="12" r="9" />
        <circle cx="12" cy="12" r="5" />
        <circle cx="12" cy="12" r="1" />
      </>
    ),
    plus: <path d="M12 5v14M5 12h14" />,
    info: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v6m0-10h.01" />
      </>
    ),
    close: <path d="m6 6 12 12M6 18 18 6" />,
  };
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={classes.icon}
    >
      {paths[name] || paths["alert-circle"]}
    </svg>
  );
}

function Label({ children, help, htmlFor }) {
  return (
    <Group component="span" gap="xs" wrap="nowrap">
      <Text
        component={htmlFor ? "label" : "span"}
        htmlFor={htmlFor}
        size="sm"
        fw={500}
      >
        {children}
      </Text>
      {help && (
        <Tooltip
          label={help}
          multiline
          w="16rem"
          withArrow
          events={{ hover: true, focus: true, touch: true }}
        >
          <ActionIcon
            type="button"
            size="sm"
            variant="subtle"
            color="gray"
            aria-label={`About ${children}`}
          >
            <Icon name="info" />
          </ActionIcon>
        </Tooltip>
      )}
    </Group>
  );
}

function TemplateButton({ template, selected, onSelect }) {
  return (
    <UnstyledButton
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`${template.name}. ${template.description}`}
      className={classes.template}
      data-selected={selected || undefined}
      data-blank={template.id === "blank" || undefined}
    >
      <span className={classes.templateFace}>
        <Icon name={template.icon} />
        <Text component="span" size="xs">
          {template.shortName}
        </Text>
      </span>
      <span className={classes.templateDetail}>
        <Text component="span" size="xs" fw={500}>
          {template.name}
        </Text>
        <Text component="span" size="xs" c="dimmed">
          {template.description}
        </Text>
      </span>
    </UnstyledButton>
  );
}

/**
 * Reusable in a drawer or a settings page; no routing or persistence of its own.
 * initialValues: tripwire record to prefill; mode: 'create' (default) or 'edit'.
 * onSubmit(values): optional async save handler. Without it, saves are simulated.
 * onTest(traceId, values): optional async test-on-a-trace handler. Without it, the test panel
 *   shows the mock's scripted sample results.
 * onCancel(): optional parent dismissal handler.
 * Changing the mode or supplied values resets the form, but parent rerenders do not.
 */
export default function TripwireForm({
  initialValues,
  mode = "create",
  onSubmit,
  onTest,
  onCancel,
}) {
  return (
    <TripwireFormFields
      key={`${mode}:${JSON.stringify(initialValues ?? null)}`}
      initialValues={initialValues}
      mode={mode}
      onSubmit={onSubmit}
      onTest={onTest}
      onCancel={onCancel}
    />
  );
}

// An API error's details as one readable line (a 422 lists every problem).
const errorText = (error) =>
  Array.isArray(error?.detail) ? error.detail.join(" · ") : error?.message || "Unknown error";

function TripwireFormFields({ initialValues, mode, onSubmit, onTest, onCancel }) {
  const { tripwireTemplates, sampleTraces } = useStoryData();
  const editing = mode === "edit";
  // With templates hidden, a new signal starts blank instead of from the Friction template.
  const initial = tripwireTemplates.find(
    (template) =>
      template.id === (editing ? initialValues?.templateId : FEATURES.templates ? "friction" : null),
  );
  const ids = useId();
  const [templateId, setTemplateId] = useState(initial?.id || "blank");
  const [name, setName] = useState(initialValues?.name ?? "");
  const [instruction, setInstruction] = useState(
    initialValues?.instruction ?? initial?.instruction ?? "",
  );
  const [fields, setFields] = useState(() => {
    const source = initialValues?.payloadFields ?? initial?.payloadFields;
    return (source?.length ? source : [{}]).map(makeField);
  });
  const [trigger, setTrigger] = useState(initialValues?.trigger ?? "trace");
  const [spanName, setSpanName] = useState(initialValues?.spanName ?? "");
  const [filters, setFilters] = useState(() =>
    (
      initialValues?.filters ??
      (editing || !FEATURES.tokenFilter ? [] : [{ field: "total_token_count", op: "gt", value: 1000 }])
    ).map((filter) => ({
      ...filter,
      id: crypto.randomUUID(),
      operator: filter.operator ?? filter.op ?? "eq",
      value: Array.isArray(filter.value)
        ? filter.value.join(", ")
        : filter.value,
    })),
  );
  const [sampling, setSampling] = useState(initialValues?.sampling ?? 100);
  const [active, setActive] = useState(initialValues?.status !== "paused");
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState({});
  const [notice, setNotice] = useState(null);
  const [sampleId, setSampleId] = useState(null);
  const [testResult, setTestResult] = useState(null);
  const [traceId, setTraceId] = useState("");
  const [testing, setTesting] = useState(false);
  const [liveResult, setLiveResult] = useState(null); // { findings, analysis, cost } or { error }
  const sample = sampleTraces.find((item) => item.id === sampleId);
  const selectedTemplate = tripwireTemplates.find(
    (template) => template.id === templateId,
  );
  // Scripted results describe the preset only; never pretend to evaluate custom edits.
  const matchesPreset =
    selectedTemplate &&
    instruction === selectedTemplate.instruction &&
    JSON.stringify(
      fields.map(({ name, description, type, choices }) => ({
        name,
        description,
        type,
        choices: listValues(choices),
      })),
    ) === JSON.stringify(selectedTemplate.payloadFields);

  useEffect(() => {
    setTestResult(null);
    setLiveResult(null);
    setNotice(null);
  }, [
    name,
    instruction,
    fields,
    trigger,
    spanName,
    filters,
    sampling,
    sampleId,
    traceId,
    active,
  ]);
  // Existing records may contain a legacy filter not offered for new filters.
  const filterSpec = (filter) => {
    const spec = FILTERS[filter.field] ?? {
      label: filter.field,
      operators: [],
    };
    return {
      ...spec,
      operators: spec.operators.some((op) => op.value === filter.operator)
        ? spec.operators
        : [
            ...spec.operators,
            { value: filter.operator, label: filter.operator },
          ],
    };
  };
  const filterOptions = [
    ...Object.entries(OFFERED_FILTERS).map(([value, spec]) => ({
      value,
      label: spec.label,
    })),
    ...[...new Set(filters.map((filter) => filter.field))]
      .filter((field) => !OFFERED_FILTERS[field])
      .map((field) => ({ value: field, label: FILTERS[field]?.label ?? field })),
  ];
  const firstFilter = Object.keys(OFFERED_FILTERS)[0];
  function applyTemplate(template) {
    setTemplateId(template.id);
    setInstruction(template.instruction || "");
    setFields(
      (template.payloadFields?.length ? template.payloadFields : [{}]).map(
        makeField,
      ),
    );
    setErrors({});
  }
  function updateField(id, key, value) {
    setFields((current) =>
      current.map((field) =>
        field.id === id ? { ...field, [key]: value } : field,
      ),
    );
  }
  function updateFilter(id, patch) {
    setFilters((current) =>
      current.map((filter) =>
        filter.id === id ? { ...filter, ...patch } : filter,
      ),
    );
  }
  function validate() {
    const next = {};
    if (!name.trim()) next.name = "Enter a name.";
    if (!instruction.trim()) next.instruction = "Enter an instruction.";
    const seen = new Set();
    for (const field of fields) {
      const value = field.name.trim();
      if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(value))
        next[`name-${field.id}`] =
          "Use letters, numbers, and underscores; start with a letter or underscore.";
      else if (seen.has(value))
        next[`name-${field.id}`] = "Field names must be unique.";
      seen.add(value);
      if (!field.description.trim())
        next[`description-${field.id}`] = "Enter a description.";
      if (field.type === "enum" && !listValues(field.choices).length)
        next[`choices-${field.id}`] = "Enter at least one choice.";
    }
    if (trigger === "span" && !spanName.trim())
      next.span = "Enter a span name.";
    for (const filter of filters) {
      if (filter.field === "total_token_count") {
        if (
          filter.value === "" ||
          !Number.isFinite(Number(filter.value)) ||
          Number(filter.value) < 0 ||
          !Number.isInteger(Number(filter.value))
        )
          next[`filter-${filter.id}`] = "Enter a non-negative whole number.";
      } else if (!listValues(String(filter.value)).length)
        next[`filter-${filter.id}`] = "Enter a value.";
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  }
  async function submit(event) {
    event.preventDefault();
    if (saving) return;
    if (!validate()) {
      setNotice({ color: "red", text: "Check the highlighted fields." });
      return;
    }
    if (!onSubmit) {
      setNotice({
        color: "teal",
        text: editing
          ? `Changes to “${name.trim()}” are ready. Demo only: no changes were saved.`
          : `“${name.trim()}” is ready. Demo only: no signal was saved or started.`,
      });
      return;
    }
    setSaving(true);
    try {
      await onSubmit(currentValues());
      setNotice({
        color: "teal",
        text: editing ? "Changes saved." : "Signal created.",
      });
    } catch (error) {
      setNotice({
        color: "red",
        text: `Could not save: ${errorText(error)}. Your edits are still here.`,
      });
    } finally {
      setSaving(false);
    }
  }
  // What the form currently says, in the shape onSubmit and onTest receive.
  function currentValues() {
    return {
      ...initialValues,
      name: name.trim(),
      templateId,
      instruction: instruction.trim(),
      payloadFields: fields.map(({ id, choices, ...field }) => ({
        ...field,
        name: field.name.trim(),
        description: field.description.trim(),
        choices: field.type === "enum" ? listValues(choices) : [],
      })),
      trigger,
      spanName: trigger === "span" ? spanName.trim() : "",
      filters: filters.map(({ id, operator, ...filter }) => ({
        ...filter,
        op: operator,
        value:
          filter.field === "total_token_count"
            ? Number(filter.value)
            : ["span_names", "tags"].includes(filter.field)
              ? listValues(String(filter.value))
              : filter.value,
      })),
      sampling,
      status: active ? "active" : "paused",
    };
  }
  // Judges one stored trace with the form as it is now, without saving anything.
  async function testLive() {
    if (!validate()) {
      setNotice({ color: "red", text: "Check the highlighted fields." });
      return;
    }
    setTesting(true);
    try {
      const result = await onTest(traceId.trim(), currentValues());
      setLiveResult(result);
    } catch (error) {
      setLiveResult({ error: errorText(error) });
    } finally {
      setTesting(false);
    }
  }
  function test() {
    if (!sample || !matchesPreset) return;
    setTestResult({
      sampleId: sample.id,
      payload: sample.results[templateId] ?? null,
    });
  }
  const blank = {
    id: "blank",
    name: "Start from scratch",
    shortName: "Blank",
    icon: "plus",
    description: "Create a custom signal",
  };

  return (
    <form onSubmit={submit} noValidate>
      <fieldset disabled={saving} className={classes.fieldset}>
        <Stack gap="xl">
          {/* On real data, on/off is the signal page's own button (it saves at once). */}
          {editing && !onSubmit && (
            <Switch
              label="Active"
              checked={active}
              onChange={(event) => setActive(event.currentTarget.checked)}
            />
          )}
          <TextInput
            id={`${ids}-name`}
            label="Name"
            value={name}
            onChange={(event) => setName(event.currentTarget.value)}
            placeholder="Name your signal"
            maxLength={255}
            error={errors.name}
            data-autofocus
          />
          {!editing && FEATURES.templates && (
            <Stack gap="xs">
              <Label help="Prefill the form with a preset or start blank.">
                Template
              </Label>
              <SimpleGrid
                cols={{ base: 2, xs: 4 }}
                spacing="xs"
                role="group"
                aria-label="Template"
                className={classes.templateGrid}
              >
                {[...tripwireTemplates, blank].map((template) => (
                  <TemplateButton
                    key={template.id}
                    template={template}
                    selected={templateId === template.id}
                    onSelect={() => applyTemplate(template)}
                  />
                ))}
              </SimpleGrid>
            </Stack>
          )}
          <Textarea
            id={`${ids}-instruction`}
            label={
              <Label help="What to look for and what to ignore.">
                Instruction
              </Label>
            }
            value={instruction}
            onChange={(event) => setInstruction(event.currentTarget.value)}
            minRows={4}
            maxRows={10}
            autosize
            error={errors.instruction}
          />
          <Stack gap="sm">
            <Group justify="space-between">
              <Label help="The details to record in each signal event.">
                Output schema
              </Label>
              <Button
                type="button"
                size="xs"
                variant="light"
                onClick={() =>
                  setFields((current) => [...current, makeField()])
                }
              >
                + Add field
              </Button>
            </Group>
            <Text size="xs" c="dimmed">
              What should each signal event record?
            </Text>
            {fields.map((field) => (
              <Box key={field.id} className={classes.schemaRow}>
                <TextInput
                  label="Name"
                  value={field.name}
                  aria-label="Output field name"
                  error={errors[`name-${field.id}`]}
                  onChange={(event) =>
                    updateField(field.id, "name", event.currentTarget.value)
                  }
                />
                <Select
                  label="Type"
                  aria-label="Output field type"
                  allowDeselect={false}
                  data={TYPES}
                  value={field.type}
                  onChange={(value) => updateField(field.id, "type", value)}
                />
                <Textarea
                  label="Description"
                  aria-label="Output field description"
                  className={classes.description}
                  autosize
                  minRows={1}
                  value={field.description}
                  error={errors[`description-${field.id}`]}
                  onChange={(event) =>
                    updateField(
                      field.id,
                      "description",
                      event.currentTarget.value,
                    )
                  }
                />
                <ActionIcon
                  type="button"
                  variant="subtle"
                  color="gray"
                  className={classes.remove}
                  disabled={fields.length === 1}
                  aria-label={`Remove output field ${field.name || "unnamed"}`}
                  onClick={() =>
                    setFields((current) =>
                      current.filter((item) => item.id !== field.id),
                    )
                  }
                >
                  <Icon name="close" />
                </ActionIcon>
                {field.type === "enum" && (
                  <TextInput
                    className={classes.choices}
                    label="Allowed choices"
                    description="Separate choices with commas."
                    value={field.choices}
                    error={errors[`choices-${field.id}`]}
                    onChange={(event) =>
                      updateField(
                        field.id,
                        "choices",
                        event.currentTarget.value,
                      )
                    }
                  />
                )}
              </Box>
            ))}
          </Stack>
          {FEATURES.spanTrigger && <Radio.Group
            value={trigger}
            onChange={setTrigger}
            label={
              <Label help="When this signal starts analyzing a trace.">
                Triggers
              </Label>
            }
          >
            <Stack gap="sm" mt="xs">
              <Radio value="trace" label="When a trace finishes" />
              <Radio value="span" label="When a specific span finishes" />
              {trigger === "span" && (
                <TextInput
                  label={
                    <Label help="The recorded step that starts analysis when it finishes.">
                      Span name
                    </Label>
                  }
                  value={spanName}
                  error={errors.span}
                  onChange={(event) => setSpanName(event.currentTarget.value)}
                />
              )}
            </Stack>
          </Radio.Group>}
          <Stack gap="sm">
            <Group justify="space-between">
              <Label help="Only read traces matching all these conditions.">
                Filters
              </Label>
              <Button
                type="button"
                variant="subtle"
                color="gray"
                size="xs"
                onClick={() =>
                  setFilters((current) => [
                    ...current,
                    {
                      id: crypto.randomUUID(),
                      field: firstFilter,
                      operator: OFFERED_FILTERS[firstFilter].operators[0].value,
                      value: firstFilter === "status" ? "error" : "",
                    },
                  ])
                }
              >
                + Add filter
              </Button>
            </Group>
            {!filters.length && (
              <Text size="sm" c="dimmed">
                All traces are eligible.
              </Text>
            )}
            {filters.map((filter) => (
              <Stack key={filter.id} gap="xs">
                <Box className={classes.filterRow}>
                  <Select
                    aria-label="Filter field"
                    allowDeselect={false}
                    data={filterOptions}
                    value={filter.field}
                    onChange={(field) =>
                      updateFilter(filter.id, {
                        field,
                        operator: (FILTERS[field] ?? filterSpec(filter))
                          .operators[0].value,
                        value: field === "status" ? "success" : "",
                      })
                    }
                  />
                  <Select
                    aria-label="Filter operator"
                    allowDeselect={false}
                    data={filterSpec(filter).operators}
                    value={filter.operator}
                    onChange={(operator) =>
                      updateFilter(filter.id, { operator })
                    }
                  />
                  {filter.field === "total_token_count" ? (
                    <NumberInput
                      aria-label="Filter value"
                      min={0}
                      allowDecimal={false}
                      value={filter.value}
                      onChange={(value) => updateFilter(filter.id, { value })}
                    />
                  ) : filter.field === "status" ? (
                    <Select
                      aria-label="Filter value"
                      allowDeselect={false}
                      data={[
                        { value: "success", label: "Success" },
                        { value: "error", label: "Error" },
                      ]}
                      value={filter.value}
                      onChange={(value) => updateFilter(filter.id, { value })}
                    />
                  ) : (
                    <TextInput
                      aria-label="Filter value"
                      placeholder={
                        filter.field === "tags" ? "Span tags" : "Span names"
                      }
                      value={filter.value}
                      onChange={(event) =>
                        updateFilter(filter.id, {
                          value: event.currentTarget.value,
                        })
                      }
                    />
                  )}
                  <ActionIcon
                    type="button"
                    variant="subtle"
                    color="gray"
                    aria-label="Remove filter"
                    onClick={() =>
                      setFilters((current) =>
                        current.filter((item) => item.id !== filter.id),
                      )
                    }
                  >
                    <Icon name="close" />
                  </ActionIcon>
                </Box>
                {["span_names", "tags"].includes(filter.field) && (
                  <Text size="xs" c="dimmed">
                    Separate values with commas.
                  </Text>
                )}
                {errors[`filter-${filter.id}`] && (
                  <Text size="xs" c="red" role="alert">
                    {errors[`filter-${filter.id}`]}
                  </Text>
                )}
              </Stack>
            ))}
          </Stack>
          {FEATURES.sampling && <Stack gap="sm">
            <Group justify="space-between">
              <Label
                help="The percentage of matching traces to analyze."
                htmlFor={`${ids}-sampling`}
              >
                Sampling
              </Label>
              <Text size="sm">{sampling}%</Text>
            </Group>
            <Slider
              id={`${ids}-sampling`}
              aria-label="Sampling"
              thumbLabel="Sampling"
              min={5}
              max={100}
              step={5}
              value={sampling}
              onChange={setSampling}
              label={(value) => `${value}%`}
            />
            <Text size="xs" c="dimmed">
              {sampling === 100
                ? "Read every matching trace."
                : `Read ${sampling}% of matching traces.`}
            </Text>
          </Stack>}
          <Accordion variant="default">
            <Accordion.Item value="test">
              <Accordion.Control>Test on a sample trace</Accordion.Control>
              <Accordion.Panel>
                {onTest ? (
                  <Stack gap="sm">
                    <Text size="xs" c="dimmed">
                      Runs the judge on one trace Tripwire has stored, with the form as it is now.
                      Nothing is saved. Copy a trace ID from a signal's Events or Runs tab.
                    </Text>
                    <TextInput
                      label="Trace ID"
                      placeholder="e.g. 9bfa8e02-f0c5-38ed-866d-524137c4c5b1"
                      value={traceId}
                      onChange={(event) => setTraceId(event.currentTarget.value)}
                    />
                    <Group>
                      <Button type="button" variant="light" disabled={!traceId.trim()} loading={testing} onClick={testLive}>
                        Test signal
                      </Button>
                    </Group>
                    {liveResult?.error && (
                      <Alert color="red" title="Test failed" role="alert">{liveResult.error}</Alert>
                    )}
                    {liveResult && !liveResult.error && (
                      <Alert color="teal" title={liveResult.findings.length
                        ? `${liveResult.findings.length} finding${liveResult.findings.length === 1 ? "" : "s"}`
                        : "No findings for this trace"} role="status">
                        <Stack gap="sm">
                          {liveResult.findings.map((finding, i) => (
                            <Stack key={i} gap={4}>
                              <Group gap="xs">
                                <SeverityBadge severity={finding.severity} />
                                <Text size="xs" c="dimmed">Evidence: {finding.evidence_labels.join(", ") || "—"}</Text>
                              </Group>
                              {Object.entries(finding.fields).map(([key, value]) => (
                                <Text key={key} size="sm"><strong>{key}:</strong> {String(value)}</Text>
                              ))}
                            </Stack>
                          ))}
                          <Text size="xs" c="dimmed">
                            {liveResult.analysis} · {liveResult.model} · {liveResult.cost_usd == null ? "cost unknown" : formatCost(liveResult.cost_usd)}
                          </Text>
                        </Stack>
                      </Alert>
                    )}
                  </Stack>
                ) : (
                <Stack gap="sm">
                  <Text size="xs" c="dimmed">
                    Scripted examples only. The editable instruction, filters,
                    and sampling are not evaluated.
                  </Text>
                  <Select
                    label="Sample trace"
                    placeholder="Choose a sample trace"
                    value={sample?.id || null}
                    onChange={setSampleId}
                    data={sampleTraces.map((item) => ({
                      value: item.id,
                      label: `${item.name} · ${formatTime(item.time)}`,
                    }))}
                  />
                  {!sampleTraces.length && (
                    <Text size="sm" c="dimmed">
                      No sample traces exist on this story day.
                    </Text>
                  )}
                  {sample && (
                    <Stack gap="xs">
                      <Text size="sm">
                        <strong>Visitor:</strong> {sample.visitor}
                      </Text>
                      <Text size="sm">
                        <strong>Assistant:</strong> {sample.reply}
                      </Text>
                      <Text size="xs" c="dimmed">
                        {sample.evidence}
                      </Text>
                    </Stack>
                  )}
                  {!matchesPreset && (
                    <Text size="sm" c="dimmed">
                      {editing
                        ? "No recorded sample result matches these settings. Testing custom instructions needs a connected classifier."
                        : "Select an unchanged preset to view its recorded sample result. Custom instructions need a connected classifier."}
                    </Text>
                  )}
                  <Group>
                    <Button
                      type="button"
                      variant="light"
                      disabled={!sample || !matchesPreset}
                      onClick={test}
                    >
                      Test signal
                    </Button>
                  </Group>
                  {testResult && sample && (
                    <Alert
                      title="Simulated sample result"
                      color="teal"
                      role="status"
                    >
                      {testResult.payload ? (
                        <Stack gap="xs">
                          {Object.entries(testResult.payload).map(
                            ([key, value]) => (
                              <Text key={key} size="sm">
                                <strong>{key}:</strong> {String(value)}
                              </Text>
                            ),
                          )}
                        </Stack>
                      ) : (
                        "No signal event for this sample."
                      )}
                    </Alert>
                  )}
                </Stack>
                )}
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>
          {notice && (
            <Alert
              color={notice.color}
              role={notice.color === "red" ? "alert" : "status"}
            >
              {notice.text}
            </Alert>
          )}
          <Box className={classes.footer}>
            <Divider mb="md" />
            <Group justify="space-between">
              {FEATURES.backfill ? (
                <Button
                  type="button"
                  variant="light"
                  onClick={() =>
                    setNotice({
                      color: "gray",
                      text: "Backfill is planned for later. Your form inputs are unchanged.",
                    })
                  }
                >
                  Backfill
                </Button>
              ) : <span />}
              <Group gap="xs">
                {onCancel && (
                  <Button
                    type="button"
                    variant="subtle"
                    color="gray"
                    onClick={onCancel}
                  >
                    Cancel
                  </Button>
                )}
                <Button type="submit" loading={saving}>
                  {editing ? "Save changes" : "Create signal"}
                </Button>
              </Group>
            </Group>
          </Box>
        </Stack>
      </fieldset>
    </form>
  );
}
