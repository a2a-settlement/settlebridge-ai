# VPC runbook

The operator runs this profile from clean clones at `deploy/assurance/revisions.lock`. The repositories do not contain VPC addresses, credentials, or live results.

1. Fetch the revisions in `deploy/assurance/revisions.lock`. Run `assurance pins check-pin` and move the pins only after those commits contain the required paths. `assurance pins export` writes the Compose build contexts. Dockerfiles do not embed SHAs. Do not pip-install an unpinned `a2a-settlement` wheel and do not mount a workstation checkout.
2. Copy `deploy/assurance/env.example` to a file outside git. Set `ASSURANCE_ENV_LABEL`, internal URLs, and secret references.
3. Start Compose. The exchange is on the internal network only. Worker containers are not given its address or a release credential.
4. Apply `exchange/schema_updates.py` by starting the exchange (`create_all` plus the idempotent column adds). Bootstrap the operator, root budget, obligation policy, and bound accounts with the test-driver identity. The test driver does not call release during a trial.
5. `assurance preflight --dry-run` checks the label and the lock only. A live preflight also requires the exchange, payment gateway, board, and observer health endpoints. `assurance run` without `--live` is a labeled simulation and is not an exchange result.
6. Run the scripted benign and premature-payment scenarios, then opt into model repeats.
7. `assurance verify-evidence` and copy the evidence volume off the host. Teardown deletes containers and keeps the evidence volume.
8. To compare a fix, snapshot Postgres, bump the lock file, migrate, and rerun the frozen scenario ids. Rollback restores the snapshot and the previous lock.

Instant settlement, deposit, acceptance, and dispute resolve are not agent tools. Agents call `request_payment`. The gateway injects the requester key and forwards only `POST /exchange/release`.
