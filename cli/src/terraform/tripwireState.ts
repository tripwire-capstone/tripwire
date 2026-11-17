import type { TripwireConfig } from "../types/config.js";
import type { State } from "../types/terraform.js";

// infra/stack/tripwire.tf: aws_ecs_task_definition.tripwire["api"], ["poller"], ...
const SERVICES_TASK_DEFINITION = 'aws_ecs_task_definition.tripwire["';

// The image Tripwire's services run, read from the stack's Terraform state; undefined when the services
// don't exist. The state is shared (S3) and updated by every apply and destroy, whoever ran it, so this
// is the truth a local note in tripwire.json can't be (another person's destroy, an interrupted run).
export const servicesImageFromState = (state: State): string | undefined => {
  const taskDefinition = state.values?.root_module.resources?.find((resource) =>
    resource.address.startsWith(SERVICES_TASK_DEFINITION),
  );
  const containers = taskDefinition?.values.container_definitions;
  if (typeof containers !== "string") return undefined;
  try {
    const image = (JSON.parse(containers) as { image?: unknown }[])[0]?.image;
    return typeof image === "string" && image ? image : undefined;
  } catch {
    return undefined;
  }
};

// The config with its services image set to what the stack really runs (removed when nothing runs)
export const withServicesImage = (config: TripwireConfig, image: string | undefined): TripwireConfig => {
  const { servicesImage: _previous, ...rest } = config.tripwire ?? {};
  return { ...config, tripwire: image ? { ...rest, servicesImage: image } : rest };
};
