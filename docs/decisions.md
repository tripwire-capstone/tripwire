# Infrastructure decisions

Every question settled while designing and building the Terraform deploy, with the answer and the reason. Numbers follow the order the questions were asked, and new ones are added as they're settled.

Deployer-specific values (domain, emails, account ID) are left out on purpose: they live in each deployer's gitignored `terraform.tfvars`.

## Scope and workflow

**Q1. Who runs this Terraform?**

**A:** Anyone with an AWS account. Nothing is hard-coded to us.

**Why:** That's the point of the product. Everything deployer-specific goes in `terraform.tfvars`.

**Q2. Where does the code live?**

**A:** A `tripwire/` repo, with the Terraform in `infra/`.

**Why:** Infrastructure and the things it deploys change together.

**Q4. What does Terraform do, and what does a script do?**

**A:** Terraform only manages AWS resources. A `deploy.sh` script will run the actions: building images, one-off tasks, and teardown with snapshot handling.

**Why:** Actions forced into Terraform (`local-exec`) only run when a resource is created, can't be previewed in `plan`, and leave state half-done when they fail.

**Q5. How does Tripwire get its Laminar API key?**

**A:** Deferred. The scope is Laminar only for now.

**Why:** Tripwire comes back once Laminar works.

**Q6. Import the hand-built stack, or build fresh?**

**A:** Build fresh, then delete the manual stack.

**Why:** Importing carries over every manual quirk and takes longer than rebuilding.

**Q7. How many environments?**

**A:** One stack per AWS account. The `name` variable allows a second copy if needed.

**Why:** Terraform workspaces make it easy to apply to the wrong one.

**Q8. How does a deployer log in to AWS?**

**A:** With their own AWS CLI login. Nothing about credentials is in the code.

**Why:** Terraform acts as whoever the AWS CLI is logged in as.

**Q10. Build everything at once, or in stages?**

**A:** Seven stages, each applied and checked before the next.

**Why:** Small applies are easy to debug.

**Q12. Is `tripwire/` its own git repo?**

**A:** Yes, with a `.gitignore` for state, `.terraform/`, `*.tfvars` and `backend.hcl`. Pushed to GitHub after each stage.

**Why:** Keeps state and real values out of git.

**Q14. How are secrets passed to Terraform?**

**A:** In `terraform.tfvars`, which is gitignored.

**Why:** It's the simplest option. Anything passed in also ends up in state, which is why the state bucket is private and encrypted.

**Q15. Which code checks?**

**A:** `terraform fmt`, `terraform validate`, and TFLint with its AWS rules. Trivy later.

**Why:** Trivy would flag accepted trade-offs as problems before the stack even exists.

**Q18. In what order are the stages built?**

**A:** 1 bootstrap, 2 persistent, 3 network, 4 data, 5 ALB, 6 ECS, 7 backups.

**Why:** DNS goes early because delegation can take time to take effect.

**Q19. How do we work through a stage?**

**A:** Each block is explained, the user writes it, the user pastes `plan` before `apply`, and then everything is verified.

**Why:** Reading plans is the most important Terraform skill.

## State and the bootstrap stack

**Q3. Where does Terraform state live?**

**A:** In S3 with native locking (`use_lockfile`), which needs Terraform 1.10 or newer. A small `bootstrap/` config creates the bucket.

**Why:** Terraform can't keep its state in a bucket it hasn't created yet.

**Q20. What is the state bucket called?**

**A:** `<name>-tfstate-<account ID>-<region>`, built automatically.

**Why:** S3 bucket names are global. The account ID makes the name unique without the deployer picking one.

**Q21. Keep old versions of the state?**

**A:** Yes: versioning on, with old versions deleted after 90 days.

**Why:** A corrupted state can be rolled back, for pennies.

**Q22. How is the bucket protected?**

**A:** S3-managed encryption, all public access blocked, an HTTPS-only bucket policy, and `prevent_destroy`.

**Why:** A KMS key would add cost without a real benefit at this size.

**Q23. Where does bootstrap's own state live?**

**A:** In a local file that isn't committed. If it's lost, re-adopt the bucket with `terraform import`.

**Why:** Bootstrap runs once per account.

**Q24. How many state files?**

**A:** Three: `bootstrap` (local), and `persistent` and `stack` (both in the bucket).

**Why:** Only the zone and certificate need a separate lifetime from the rest.

**Q25. Which region?**

**A:** `us-east-1` by default. Every stack must use the same region.

**Why:** It's where we already work, and usually the cheapest.

**Q26. Which name prefix?**

**A:** `tripwire`.

**Why:** It's the product's name. Renaming later means recreating everything.

## Domain, DNS and certificate

**Q9. Which domain?**

**A:** The deployer's own domain. We use a subdomain of a personal site.

**Why:** HTTPS and gRPC on the ALB need a certificate, and a certificate needs a domain.

**Q11. Can Laminar be served at a sub-path like `/laminar`?**

**A:** No, it gets its own subdomain.

**Why:** Laminar's public frontend image bakes its base path in when it's built. Only the private enterprise build offers a sub-path image.

**Q13. Should the zone and certificate survive a teardown?**

**A:** Yes. They live in a separate `persistent/` stack.

**Why:** A destroyed zone comes back with new nameservers, which means editing the DNS provider again.

**Q16. The domain's DNS is at the registrar (Namecheap). How do Laminar's records get managed?**

**A:** Delegate one subdomain to a Route 53 zone, by adding 4 NS records at the registrar once.

**Why:** After that, every rebuild works with no manual DNS edits. Moving the whole domain to Route 53 was rejected: it would break the registrar's email forwarding and move the personal site's DNS too.

**Q17. Which host names?**

**A:** The delegated zone is Laminar's subdomain itself, and Laminar sits at the zone's own name.

**Why:** Simplest for now. Tripwire will need its own delegation later.

**Q27. The certificate can't validate until the NS records exist. How is that sequenced?**

**A:** First `terraform apply -target=aws_route53_zone.main`, then add the NS records, then a full apply.

**Why:** `-target` is meant for exceptional cases, and this one-time setup is one.

**Q28. Which certificate modes are supported?**

**A:** Creating a delegated zone, or using an existing Route 53 zone (`zone_id`). Bringing your own certificate comes later.

**Why:** One code path per real need.

**Q29. Which names does the certificate cover?**

**A:** Exactly Laminar's host name.

**Why:** Nothing else needs it yet. A wildcard can be added later for free, since it uses the same validation record.

**Q30. Protect the zone and certificate from deletion?**

**A:** `prevent_destroy` on the zone only.

**Why:** The certificate can be recreated in minutes against the same validation record.

## Network

**Q31. What address range does the VPC use?**

**A:** `10.20.0.0/16`, with public subnets `10.20.0.0/24` and `10.20.1.0/24`, and private subnets `10.20.10.0/24` and `10.20.11.0/24`.

**Why:** It doesn't clash with the default VPC (`172.31.0.0/16`).

**Q32. Use the community VPC module, or write it by hand?**

**A:** By hand.

**Why:** The network is small, and every piece stays visible while learning.

**Q33. Where do the security groups live?**

**A:** All in one `security_groups.tf`.

**Why:** The rules reference each other, and together they read as the "each layer only trusts the one in front" chain.

**Q34. Add TFLint's AWS rules?**

**A:** Yes, at a pinned version. TFLint itself installs from its own tap: `brew install --cask terraform-linters/tap/tflint`.

**Why:** Homebrew's main catalog no longer has it.

**Q40. Add a NAT gateway?**

**A:** Yes. The ECS tasks and the data box live in private subnets with no public IPs.

**Why:** It's the standard production layout: nothing but the ALB is reachable from the internet. It costs about $29/month more.

**Q42. One zonal NAT, or a regional NAT?**

**A:** One classic NAT in the first AZ.

**Why:** RDS and the data box are pinned to that AZ anyway, so a second NAT wouldn't keep the stack up if the AZ failed.

**Q43 and Q46. A `private_network` switch, or always private?**

**A:** Always private.

**Why:** One layout to test instead of two. A cost-saving switch can come later if someone needs it.

**Q44. When does the NAT go in?**

**A:** In stage 3b, before the data stage.

**Why:** The database and data box then go straight into the private subnets.

## Database, data box and secrets

**Q35. What are the RDS settings?**

**A:** PostgreSQL 16 on `db.t4g.micro`, 20 GB of gp3 storage that can grow to 50 GB, Single-AZ in the first AZ, not public, database `laminar`, user `lmnr_admin`.

**Why:** It matches Laminar's compose file, at the smallest size that works.

**Q36 and Q45. How do we protect data and still allow test teardowns?**

**A:** No deletion protection. A teardown always takes final snapshots of the database and the data volume (`keep_final_snapshots`, default `true`). On the next create, `db_snapshot_identifier` and `data_volume_snapshot_id` restore from them. Both use `ignore_changes`, so changing them later can't replace live data.

**Why:** Teardowns always succeed, and the data can always be brought back. `prevent_destroy` can't take a variable, so it can't be the on/off switch.

**Q47. How many snapshot sets are kept?**

**A:** Exactly one. `deploy.sh destroy` records the old set, runs the teardown, waits until the new set is available, then deletes the old set. A `purge-snapshots` command removes the last one.

**Why:** Terraform can't delete final snapshots, because they never enter its state. Deleting the old set only after the new one exists means a failed teardown never leaves zero.

**Q48. Is one snapshot set enough?**

**A:** Yes, for teardown and restore.

**Why:** Stage 7's AWS Backup gives hourly and daily history for real use.

**Q37. Which machine image does the data box use?**

**A:** The latest Amazon Linux 2023 for the instance's chip, with `ignore_changes = [ami]`.

**Why:** A new Amazon Linux release must not replace the running box on its own.

**Q38. How does the data box get its software?**

**A:** A user data script built with `templatefile()`. It installs Docker and the Compose plugin, mounts `/data`, and runs ClickHouse `26.5` and Quickwit `v0.9.1`. Laminar's two ClickHouse XML configs are copied into the repo.

**Why:** No image-building tooling for a single box, and a change to Laminar's configs shows up as a diff to review.

**Q39. Who generates the passwords?**

**A:** `random_password`, stored as SSM `SecureString` parameters. The data box reads its password from SSM at boot. No RDS-managed rotation.

**Why:** Automatic rotation would silently break the connection string Laminar reads once at startup.

**Q41. How are the files split?**

**A:** `rds.tf`, `data_box.tf` (plus `files/`), and `secrets.tf`.

**Why:** Stage 6 reads the same secrets, so they get their own file.

**Q49. Stay on the AWS free plan?**

**A:** Yes. Each free-plan limit becomes a variable, such as `db_backup_retention_days` (1 on the free plan, 7 by default).

**Why:** The free plan rejected 7 days of RDS backups. Variables keep the code working for both paid and free accounts.

**Q50. Which instance type for our data box?**

**A:** `c7i-flex.large` (x86, 4 GB).

**Why:** The free plan only allows a few instance types, and `t4g.medium` isn't one of them. 4 GB is proven by the manual build.

**Q51. What's the default for other deployers?**

**A:** `t4g.medium` (Arm, 4 GB). Terraform detects the chip and picks the matching image and Compose binary.

**Why:** It's the cheapest 4 GB type on a paid account.

**Q52. What happens to the manual stack?**

**A:** Delete it once the Terraform-deployed Laminar works. Until then, stop it when it's not in use.

**Why:** Running both doubles the credits used.

**Q53. Should the data box keep its IP when it's replaced?**

**A:** Yes: a fixed `10.20.10.10`, set with `cidrhost`.

**Why:** Otherwise every data box replacement would also force a Laminar redeploy.

## Load balancer and access

**Q54. How does the stack read the zone and certificate?**

**A:** With `terraform_remote_state` on the persistent stack. The bucket name is rebuilt from the naming rule.

**Why:** It reads exactly what persistent produced, and works in both zone modes.

**Q55. Which TLS policy?**

**A:** `ELBSecurityPolicy-TLS13-1-2-PQ-2025-09`.

**Why:** It allows the same protocols as the older 2021 policy (TLS 1.2 and 1.3), and adds post-quantum key exchange for clients that support it.

**Q56. Does the ALB get deletion protection?**

**A:** No.

**Why:** Teardowns must succeed, and the ALB holds no data.

**Q57. When is gRPC through the ALB tested?**

**A:** In stage 6, once app-server is running.

**Why:** If only gRPC fails, the UI and HTTP ingestion still work.

**Q58. Self-hosted Laminar has a passwordless email login (anyone can sign in as any email) and no way to restrict sign-ups. How do we make it invite-only?**

**A:** A Cognito login at the ALB, in front of the UI, with self-sign-up turned off and users listed in `laminar_users`. SDK ingestion (`/v1/*` and gRPC) stays ungated and is protected by the project API key.

**Why:** Strangers can't reach Laminar at all. GitHub sign-in was rejected: it stops impersonation, but anyone with a GitHub account could sign up. Known limits: invited users could still sign in as each other behind the gate, and `lmnr-cli setup` can't get through it.

**Q64. Who gets access first?**

**A:** Only the deployer. Teammates are one line each, later.

**Why:** It keeps the first test simple.

## Laminar on ECS

**Q59. Which Laminar version?**

**A:** `v0.3.3` for both images, set by one variable.

**Why:** Pinned, so a redeploy never silently upgrades Laminar. The frontend and app-server must always match.

**Q60. How does the frontend get the RDS certificate bundle?**

**A:** Downloaded at plan time from AWS's trust store for the deployer's region (with the `http` provider), and passed in as `DATABASE_SSL_ROOT_CERT`.

**Why:** It works in any region, and the bundle is public.

**Q61. What about Laminar's remaining secrets?**

**A:** `SHARED_SECRET_TOKEN`, `AEAD_SECRET_KEY` and the auth secret, each 32 random bytes as hex, stored in SSM. Laminar's own AI features stay off for now.

**Why:** These are the values the manual build made with `openssl rand -hex 32`. Never regenerate `AEAD_SECRET_KEY` once there's data: it encrypts stored provider keys.

**Q62. What are the task and service settings?**

**A:** One ARM64 task with 1 vCPU and 2 GB, in the private subnets, registered in three ALB target groups. A 600-second health check grace period, a circuit breaker with rollback, ECS Exec on, and logs kept 7 days.

**Why:** The grace period covers the ClickHouse migrations on first boot.

**Q63. What counts as "stage 6 works"?**

**A:** All three target groups healthy (gRPC included), the UI loads and sign-in works, and a trace sent over gRPC and one over HTTPS both appear.

**Why:** Each point checks a different path through the stack.

**Q65. Which ClickHouse user does Laminar's read-only connection use?**

**A:** The same `ch_user` and password as the main connection, passed as `CLICKHOUSE_RO_USER` and `CLICKHOUSE_RO_PASSWORD` to both containers. A separate read-only ClickHouse user is a possible hardening step later.

**Why:** It's what Laminar's own example config does. app-server's SQL validator already limits these queries to `SELECT` within the user's project. A ClickHouse user in strict read-only mode might block settings Laminar's queries rely on, so it needs testing first.

## Findings along the way

These weren't questions, but they changed the design.

- **The ClickHouse read-only connection isn't optional after all.** app-server starts without it, but its SQL endpoint, which the UI uses for trace lists and the SQL editor, fails with "ClickHouse client is not configured." It only turns on when both `CLICKHOUSE_RO_USER` and `CLICKHOUSE_RO_PASSWORD` reach app-server (see Q65).
- **Laminar's ClickHouse user is `ch_user`**, as in its `.env.example`.
- **The data box's boot script leaked the ClickHouse password.** `set -x` printed it into the boot log and the EC2 console output. It's fixed with `set +x` around the secret, and the password was rotated with `-replace`.
- **In a `.tftpl` file, every `${` is a Terraform expression, comments included.** Write a literal one as `$${`.
- **Changing an ALB action's type in place** gives a one-time provider warning about the old `target_group_arn`. State cleans itself up.

## Still to decide

- **Stage 7:** the AWS Backup plan (hourly and daily), failed-backup alerts, and whether the free plan allows them.
- **`deploy.sh`:** building, teardown with snapshot handling, and `purge-snapshots`.
- **A `README.md`** for other deployers.
- **Tripwire itself:** its containers, its database, and how it gets a Laminar API key.
