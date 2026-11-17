import * as client from "./client";
import { definitionToApi, settingsFromApi, signalFromApi, templateToApi } from "./adapt";

// Each takes the form's saved values and returns the saved signal in the pages' shape.
export const createSignal = (values) =>
  client
    .createSignal({
      ...definitionToApi(values),
      template: templateToApi(values.templateId),
    })
    .then(signalFromApi);

export const editSignal = (id, values) =>
  client.editSignal(id, definitionToApi(values)).then(signalFromApi);

export const setActive = (id, active) =>
  client.setActive(id, active).then(signalFromApi);

// Judges one stored trace with the form's (unsaved) definition. Saves nothing.
export const testRun = (laminarTraceId, values) =>
  client.testRun(laminarTraceId, definitionToApi(values));

// Settings: the API checks the key with Laminar or OpenAI before saving. Both return the new settings.
export const saveKey = (kind, apiKey) => client.setKey(kind, apiKey).then(settingsFromApi);
export const clearKey = (kind) => client.clearKey(kind).then(settingsFromApi);
