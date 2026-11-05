# tripwire CLI

Deploys and tears down Tripwire (Laminar on AWS). Runs from source.

**Use your own AWS account:** one deployment per account. Costs about
$135/month while deployed. You need a domain you control.

## Requirements

Node 20+, Terraform 1.10+, AWS CLI 2.32+. Run `aws login` right before
each deploy or destroy, because a login that expires mid-run stops it.

## Setup

```bash
npm --prefix cli install
npm --prefix cli test
```

## Commands (run from the repo root)

```bash
npm --prefix cli run dev -- init            # region, domain, emails
npm --prefix cli run dev -- deploy --lite   # build or restore
npm --prefix cli run dev -- destroy         # tear down, keep snapshots
```

- **deploy:** if it creates a new DNS zone, it prints name servers.
  Set them at your registrar; it continues once DNS matches.
  AWS emails you a temporary password for the Laminar URL.
- **destroy:** type `delete` to confirm. The DNS zone, secrets and
  newest snapshots are kept.
- **deploy after destroy:** restores your data. Add `--fresh` to start
  empty.

## Test checklist

- [ ] init, then deploy: the Laminar URL loads and you can sign in
- [ ] deploy again: no changes
- [ ] destroy, then deploy: data restored, and you can sign in
- [ ] destroy again to stop the costs

## Troubleshooting

- Logs: `~/.tripwire/tripwire/logs/`. Send the log with any bug report.
- `ExpiredTokenException`: run `aws login` and re-run. Re-running is safe.
- Not built yet: `status`, `users`, `purge-snapshots`.