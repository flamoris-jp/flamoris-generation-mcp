# Automatic speech duration distribution — 2026-10-17

Prepares 1.0.10 with Controller 1.0.7 and matched Docker frozen pin/component
labels. Omitted/null seconds is automatic; explicit numeric recipes stay fixed.
Matched Studio 1.0.10 is required by its strict descriptor contract. Normal
fake-provider checks and both real distribution architectures precede release;
publication and live output-quality acceptance remain pending. No new tools,
provider activation, inference retry or job/asset storage change.

# Resident speech timeout distribution — 2026-10-16

Source target Generation 1.0.9 embeds Controller 1.0.6 at an immutable commit, extends update compatibility from 1.0.8 and uses the published 1.0.8 predecessor in distribution CI. The 25 external tools, runtime/configuration/data placement and unknown/no-replay behavior remain unchanged. Existing explicit timeout settings are preserved; adjust the operator value separately if required. Offline reconciliation needs independently confirmed runtime settlement and a stopped authority; installation does not release unknown work. Docker frozen dependencies and OCI component/release labels are aligned with the immutable Controller pin; CI independently asserts the installed Controller distribution version. Distribution acceptance uses the cleanup-corrected Updater source. Publication and real-host reconciliation/update remain pending.

---

# Distribution component contract (2026-10-12)

- Generation 1.0.3 declares embedded Controller 1.0.1 in its install recipe and requires Updater 1.0.9 first. Distribution CI uses the immutable reviewed Updater component-validation source; it runs actual disposable fresh installations on amd64/arm64.
- This change does not download models or configure ComfyUI/shared inputs on a real host.

# Production reference integration — 2026-10-12

Matches Controller #13/#14 for IPAdapter appearance/style, prepared-map ControlNet, schema 8 and four managed image roles. No new MCP tools or job authority. Includes an external-MCP register/upload/build/submit/result test and ipadapter model discovery. Source target 1.0.3 / update from 1.0.2 is release preparation; no publication, host change or actual inference. Local regression and CI evidence are attached to the PR.

---

# Repository-owned distribution preparation — 2026-10-11

The operator explicitly requested preparing this repository for individual Updater installation. Release 1.0.1 adds an app-owned installer definition, per-platform packaging, direct Release URLs and typed/profile validation. No centralized app distribution, live deployment, DB/data/credential change or provider invocation is performed. CI/publication and actual-host acceptance will be reported separately. This is a new-install recipe; no unverified compatibility edge is declared.

---

# Progress

## Updater adoption — 2026-10-08

Entry release target: **1.0.0**. Independent mTLS Owner and durable admission
source are implemented; source review/fixes and CI integration are complete.
PR [#74](https://github.com/flamoris-jp/flamoris-generation-mcp/pull/74) is prepared for human
review. Release publication and real-host adoption remain pending.

## Verification

Local full suite: **196 passed**. All seven application wheel-from-sdist builds and
declared Owner entrypoint/module checks passed. The operator made Updater public,
resolving the initial SDK download 404. That adoption checkpoint used SDK source
pinned to
`d9f010a92ff6e8a1e7a3b7fad8817850bdfb72cd` (Updater PR #6).

[CI run 37766397836](https://github.com/flamoris-jp/flamoris-generation-mcp/actions/runs/37766397836):
196 passed; lint, package, installed-wheel and container checks succeeded. The facade delegates update ownership to its one embedded Controller authority.
These results precede this progress-only commit; package/source dependency pins
are unchanged. Cross-repository findings and exact evidence are recorded in
Updater [ADOPTION_REVIEW.md](https://github.com/flamoris-jp/flamoris-updater/blob/feat/application-entry-v1/docs/ADOPTION_REVIEW.md).

## Operational boundary

The entry path preserves already current application schemas and retained data;
unsupported schemas/resources and unknown outcomes remain blocked. No data/schema
initialization, private profile/trust provisioning, release publication, live
provider call, real-host update, enrollment or automatic merge occurred. Native
deployment overlays and matched dependencies remain deployment-owned.
See [Updater contract](docs/UPDATER.md).

## Pre-deployment dependency refresh

The SDK now targets merged Updater revision
`797d6f4e7bd4089e7c162fa50c10a0afae68370a`, and the packaged Controller
targets merged retained-resource correction
`eacf1086e28ca3eeb13b4297d4343fdec1163429`. Review and CI at these exact
revisions are pending. No runtime, release, data or host state was changed.
