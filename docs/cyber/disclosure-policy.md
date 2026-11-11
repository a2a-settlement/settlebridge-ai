# Disclosure policy

This policy covers findings the SettleBridge fix verifier makes in someone else's code.

Launch scope is pure-Python projects published on PyPI.

## Opt-in

The verifier runs only on a repository whose owner has opted in. The repository link is enabled, and the owner has turned the verifier on. A repository that has not opted in is left alone.

## Reports on GitHub

The verifier opens a pull request only after the repository owner asks for one. The verifier opens a GitHub issue only after the repository owner asks for one.

## Private by default

A finding starts private. A confirmed incomplete fix in another project's code is an undisclosed vulnerability. The repository owner and SettleBridge operators can see the details in every state. Anyone else can see the details only after the finding is disclosed.

A finding moves from private, to notified, to embargoed, and then to disclosed. The maintainer can agree to publish from the notified state, before an embargo date is set.

## Embargo

We notify the maintainer, then keep the finding under embargo. The default embargo is 90 days from notification. The maintainer and SettleBridge can agree to a longer embargo. We publish earlier when the maintainer agrees. We publish earlier when the issue is being actively exploited.

## Receipts and reproducers

While a finding is embargoed, a receipt states the verdict only. The receipt leaves out the reproducer, the affected code, and the steps that trigger the bug. A reproducer is published only after the finding is disclosed.

## Contact

A maintainer reaches us at security@a2a-settlement.org. Use that address to confirm a notification, agree to publication, or ask for more time.

## Safe harbor

Good-faith research that follows this policy is authorized on a repository that has opted in. Keep the finding private, avoid harm to users, and avoid disruption of the service. SettleBridge will not bring a legal claim over that good-faith work. This safe harbor binds SettleBridge. Another party is bound when that party has agreed to the same terms. Research on a repository that has not opted in is outside this policy.
