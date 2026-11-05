import * as p from "@clack/prompts";
import { checkLogin } from "../checks.js";
import { readConfig, writeConfig } from "../config.js";
import { UserError } from "../errors.js";
import { syncInfra } from "../infra.js";
import { bundledInfraDir, deploymentDir } from "../paths.js";
import { Log } from "../run.js";
import {
  deleteSnapshots,
  formatDate,
  isScheduled,
  listSnapshots,
  planRotation,
  waitForSet,
} from "../snapshots.js";
import { discardPlan, tfApply, tfInit, tfPlan, tfPlanJson, writeSettings } from "../terraform.js";
import { answer, step } from "../ui.js";

export interface DestroyOptions {
  yes?: boolean;
}

export async function destroy(options: DestroyOptions): Promise<void> {
  p.intro("tripwire destroy");
  const config = await readConfig();
  if (!config) throw new UserError("Run `tripwire init` first.");

  const log = new Log("destroy");
  await step(
    "Checking Terraform and your AWS login",
    () => checkLogin(log, config),
    () => `AWS account ${config.accountId}`,
  );

  await syncInfra(bundledInfraDir(), deploymentDir());
  await writeSettings(config);

  const changed = await step("stack: planning", async () => {
    await tfInit(log, "stack");
    return tfPlan(log, "stack", ["-destroy"]);
  });
  if (!changed) {
    await discardPlan("stack");
    p.outro("Nothing to destroy: the stack isn't deployed.");
    return;
  }

  const plan = await tfPlanJson(log, "stack");
  const count = (plan.resource_changes ?? []).filter((c) =>
    c.change.actions.includes("delete"),
  ).length;
  const before = await step("Finding snapshots", () => listSnapshots(log, config.region));
  const oldFinals = before.db.length + before.volumes.filter((s) => !isScheduled(s)).length;
  const scheduled = before.volumes.filter(isScheduled).length;

  p.log.warn(
    [
      `This deletes the stack: ${count} resources, including Laminar,`,
      "the database, the data box and the load balancer.",
      "Kept: the DNS zone, the certificate and the state bucket.",
      "",
      "Final snapshots of the database and data volume are taken first.",
      `Once they're ready, ${oldFinals} old final and ${scheduled} scheduled`,
      "snapshots are deleted.",
    ].join("\n"),
  );

  if (!options.yes) {
    answer(
      await p.text({
        message: 'Type "delete" to proceed',
        validate: (v) => (v === "delete" ? undefined : 'Type "delete", or Ctrl-C to cancel'),
      }),
    );
  }

  await step("stack: destroying", () => tfApply(log, "stack"), () => "stack: destroyed");

  const after = await listSnapshots(log, config.region);
  const rotation = planRotation(before, after);
  await step(
    "Waiting for the final snapshots",
    () => waitForSet(log, config.region, rotation.keep),
    () => "Final snapshots ready",
  );
  await step(
    "Deleting old snapshots",
    () => deleteSnapshots(log, config.region, rotation),
    () => `Deleted ${rotation.deleteDb.length + rotation.deleteVolumes.length} old snapshots`,
  );

  await writeConfig({ ...config, lastDestroy: { at: new Date().toISOString() } });
  p.outro(
    `Destroyed. Snapshots from ${formatDate(rotation.keep.volume.created)} are kept.\n` +
      "Run `tripwire deploy --lite` to restore.",
  );
}