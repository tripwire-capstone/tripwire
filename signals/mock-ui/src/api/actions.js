import * as client from "./client";
import { definitionToApi, settingsFromApi, signalFromApi, slackFromApi, templateToApi } from "./adapt";

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

// Slack: connecting posts a test message and saves the webhook only if Slack accepts it; replacing
// or disconnecting posts a goodbye to the old channel. Both return the new Slack status.
export const connectSlack = (webhookUrl) => client.setSlack(webhookUrl).then(slackFromApi);
export const disconnectSlack = () => client.clearSlack().then(slackFromApi);
