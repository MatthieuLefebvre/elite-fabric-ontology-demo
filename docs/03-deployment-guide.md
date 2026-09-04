# Deployment guide: supported core, explicit live blockers

Public contracts were checked on **2026-09-04**. This implementation has not been
live-certified. Actual notebook templates, a portable ontology adapter, and offline
tests are included; local success does not prove service acceptance, delegated
authorization, or Word integration. The local suite and lint pass on Python 3.12.10;
no live tenant credentials were supplied and neither notebook ran on Fabric Spark.

## Capability matrix

| Step | Operation | Status and truth boundary |
|---|---|---|
| 01 | Workspaces on existing capacity | Documented v1 POST with displayName, capacityId and ownership description. No capacity provisioning. |
| 02 | Provider bronze, silver, gold_harbor, gold_kestrel; consumer harbor_data, kestrel_data | Documented non-schema lakehouse creation. Firm gold is separated physically by lakehouse. |
| 03 | Synthetic Parquet, schema and manifest into provider Bronze Files | ADLS Gen2 SDK, provider identity only; checksum/path validation; manifest last; no expected-answer upload. |
| 04 | Upload and run supplied transformation notebooks | Public ipynb/InlineBase64 definitions, in-memory CONFIG_JSON injection, bounded job polling; local validation precedes writes. |
| 05 | External-share create/invite/accept | **BLOCKED:** no verified public create/accept contract. Manual provider invitation and recipient acceptance required. No copy fallback. |
| 06 | Same-tenant simulation shortcuts | Documented POST with GET-first exact-target checks. External target inspection cannot certify source binding. |
| 07 | Consumer ontology upsert | REST plumbing and adapter seam implemented; **BLOCKED before binding** by raw-graph authorization validation. |
| 08 | Consumer data agent backed by ontology | Generic REST definition plumbing; **BLOCKED:** documented datasource enum lacks ontology. No fake agent or guessed discriminator. |

Full live preflight reports known blockers **before resource creation**. Selecting
`--through-step 4` explicitly requests core preparation, not a working end-to-end
agent. There is no silent skipping of blockers. A successful dry-run only certifies
that a symbolic plan was produced, not any cloud capability or authorization claim.

## Local setup and safe entry points

Use Python **3.11 or newer**. [scripts/setup.ps1](../scripts/setup.ps1) (Windows) and
[scripts/setup.sh](../scripts/setup.sh) (Unix) create a local virtual environment and install the
existing pinned direct dependencies from [requirements.txt](../requirements.txt).
They check dependency consistency. Transitive dependencies are not a complete
hash-locked supply-chain lockfile. No global install or cloud configuration occurs.

[scripts/run_all.ps1](../scripts/run_all.ps1) and [scripts/run_all.sh](../scripts/run_all.sh) run local generation,
tests, then deployment planning. **They always pass --dry-run**, even without the
convenience dry-run flag. Generation writes local data; deployment dry-run itself
does not write files, acquire tokens or make network requests. Live execution must
deliberately invoke [fabric/deploy.py](../fabric/deploy.py) with the local virtual
environment's interpreter, outside the wrapper.

All setup/run wrappers resolve the repository root from their own location under
`scripts`, so they can be invoked from another working directory.

The numbered entry points below expose `run(ctx)` and a standalone `main()` that
delegates to the shared CLI's fixed `--step N` selection. They retain the same
preflight, prerequisite checks and journal lock; they are not bypasses. Step 99
delegates to the existing ownership-safe `--teardown` workflow.

- [01_create_workspaces.py](../fabric/steps/01_create_workspaces.py)
- [02_create_lakehouses.py](../fabric/steps/02_create_lakehouses.py)
- [03_load_bronze.py](../fabric/steps/03_load_bronze.py)
- [04_run_transformations.py](../fabric/steps/04_run_transformations.py)
- [05_create_external_share.py](../fabric/steps/05_create_external_share.py)
- [06_create_shortcuts.py](../fabric/steps/06_create_shortcuts.py)
- [07_deploy_ontology.py](../fabric/steps/07_deploy_ontology.py)
- [08_create_data_agent.py](../fabric/steps/08_create_data_agent.py)
- [99_teardown.py](../fabric/steps/99_teardown.py)

Shared CLI options:

- `--config PATH`: dotenv file; process environment overrides it. An explicit missing
  file fails. Without this option, an optional repository-root dotenv is used. Paths
  in configuration resolve relative to the repository, not the current directory.
  Environment-variable interpolation is disabled; secrets are literal values.
- `--dry-run`: complete offline plans with symbolic `${resource.id}`, not fake UUIDs.
- `--through-step N`: steps 01 through N. Choose 4 for core; 6 only for explicitly
  labeled simulation shortcuts after arranging source permissions.
- `--step N`: only that numbered step, with earlier live journal prerequisites required.
  Each numbered module also exposes `run(ctx)` for a trusted embedding orchestrator.
- `--single-tenant-simulation`: exactly two workspaces, described below.
- `--allow-shared-consumer-tenant`: allow weaker customer-tenant isolation explicitly.
- `--preflight-only`: read-only checks, also available through
  [scripts/validate_environment.py](../scripts/validate_environment.py) with these flags.
- `--teardown`: remove eligible owned resources; combine with --dry-run for a plan.
  Incompatible with step selection and preflight-only.

Exit codes: **0** selected work/plan completed; **2** configuration/service/capability
blocker; **1** unexpected suppressed failure. No exit code certifies partner security,
graph refresh, delegated evaluation or Word publishing.

## Identity, topology and permissions

[.env.example](../.env.example) documents all consumed settings and delegated evaluator
integration variables. Keep credentials outside source control. Provider, Harbor and
Kestrel each use separate ClientSecretCredential and FabricClient objects. No default
credential chain or provider fallback is used. Scopes are:

- Fabric: `https://api.fabric.microsoft.com/.default`
- OneLake: `https://storage.azure.com/.default`

Deployment application identities are not the delegated evaluator. The evaluator uses
`HARBOR_PARTNER_OBJECT_ID`, `HARBOR_USER_CLIENT_ID` and Harbor's tenant for device login,
not provider credentials or an application token. Kestrel equivalents are reserved.
User object IDs are actual immutable Entra IDs, not generator timekeeper/app IDs;
the operator must verify that mapping. Deployment does not handle delegated device
codes. Evaluation displays the device challenge only in the local console; never
paste it or a token into an agent conversation or store it in a report.

### Normal: three independent tenants, three workspaces

Provider owns synthetic-only source and transformations. Each firm has its own
consumer workspace in its own tenant, on an accessible capacity. The provider never
queries consumer user data. All storage uploads and Spark jobs are provider-side;
consumer REST operations use the corresponding consumer token client.

### Shared consumers: two tenants, three workspaces, weaker tenant isolation

The explicit shared-consumer flag allows Harbor and Kestrel in one consumer tenant
but retains separate workspaces. The consumer tenant must differ from the provider.
Output labels this **workspace isolation only**, not independent customer tenants.

### Simulation: one tenant, exactly two workspaces

All three configured identities must be in one tenant. One provider workspace and
exactly one consumer workspace are used. That consumer workspace contains **two
separate firm lakehouses**. Harbor/Kestrel capacity IDs and optional adopted workspace
IDs must match. This is **weaker within-consumer security**: broad workspace roles
may expose both firms. It is not partner-level authorization or firm tenant isolation.

Harbor creates the shared consumer workspace. Kestrel must already have suitable
permissions before it can use it. For unattended simulation, pre-create/adopt this
workspace with both principals authorized, or explicitly configure the same deployment
principal for both consumer roles. Token clients remain separate. No roles are silently
granted. For step 06, give each consumer principal read access to its corresponding
provider gold source; do not broadly grant bronze/silver. Missing permissions cause
real failures, not a claimed successful shortcut.

External share links cannot be accepted within the provider tenant. Simulation step
05 is explicitly not applicable; step 06 uses documented OneLake shortcuts instead.

| Identity/task | Required administrator review |
|---|---|
| Each deployment service principal | Fabric API and workspace creation tenant settings; allowed/excluded group membership and effective scopes. |
| Create workspace/assign capacity | Workspace creation permission plus Contributor or Admin on active existing capacity. |
| Create/manage items and execute notebooks | Appropriate workspace role, normally Contributor or stronger; workspace deletion may need Admin. |
| Provider storage upload | Provider lakehouse write permission, never customer operational-data access. |
| External-share human creator | Enabled creation setting; Read and Reshare on firm gold item. |
| External-share human recipient | Enabled acceptance setting; rights to select its own consumer lakehouse. |
| Delegated partner | Least-privilege data-plane rights and tested identity propagation. Do not grant workspace Contributor to make an evaluation pass. |
| Previews and M365 | Supported capacity SKU/region, preview enablement, AI-region approval, Fabric and M365 licenses. |

### Exact tenant switch display names and scope

In **Fabric Admin portal → Tenant settings**, a Fabric administrator reviews the
following switches separately in provider, Harbor, and Kestrel. Scope enablement to
approved groups; check excluded groups, delegated capacity overrides, and propagation.
Display titles are **not** internal API `settingName` identifiers. Populate
`*_CORE_SETTING_NAMES` only from the target tenant's actual settings response.

| Switch display name | Where / why / source |
|---|---|
| **Service principals can call Fabric public APIs** | Each deployment tenant; permit approved service principals to call permission-protected APIs. [Developer settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer#service-principals-can-call-fabric-public-apis). |
| **Service principals can create workspaces, connections, and deployment pipelines** | Each tenant where a deployment principal creates workspaces; distinct from the preceding CRUD switch. [Developer settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer#service-principals-can-create-workspaces-connections-and-deployment-pipelines). |
| **Users can create Fabric items** | Each tenant and effective capacity override; enables creation, not automatic data access. [Fabric enablement](https://learn.microsoft.com/en-us/fabric/admin/fabric-switch). |
| **External data sharing** | Provider; scopes invitation creators. The requested wording **Users can create external data shares** describes the permission, but the checked Learn pages label the switch **External data sharing**. Verify the actual portal title rather than inventing an internal ID. [Enable sharing](https://learn.microsoft.com/en-us/fabric/governance/external-data-sharing-enable), [export/sharing settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-export-sharing#external-data-sharing). |
| **Users can accept external data shares** | Each external consumer; scope to intended accepting users. Not the semantic-model B2B switch. [Enable sharing](https://learn.microsoft.com/en-us/fabric/governance/external-data-sharing-enable). |
| **Users can access data stored in OneLake with apps external to Fabric** | Provider for local ADLS SDK upload; review external-engine/shortcut use separately. [OneLake security](https://learn.microsoft.com/en-us/fabric/onelake/security/get-started-security#allow-apps-running-outside-of-fabric-to-access-data). |
| **Enable Ontology item (preview)** | Consumer tenants for ontology creation; does not authorize raw graph rows. [Ontology settings](https://learn.microsoft.com/en-us/fabric/iq/ontology/overview-tenant-settings). |
| **Users can use Copilot and other features powered by Azure OpenAI** | Consumer data agents and any provider notebook Copilot use; check tenant and delegated capacity settings. [Copilot settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-copilot). |
| **Data sent to Azure OpenAI can be processed outside your capacity's geographic region, compliance boundary, or national cloud instance** | Conditional: data-agent guidance requires this outside the EU data boundary and US. Obtain compliance approval; do not enable indiscriminately. [Data-agent settings](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-tenant-settings). |
| **Data sent to Azure OpenAI can be stored outside your capacity's geographic region, compliance boundary, or national cloud instance** | Conditional for notebook Copilot/data-agent conversation storage outside the EU data boundary and US; approve retention/residency separately from processing. [Copilot settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-copilot). |
| **Capacities can be designated as Copilot in Fabric capacities** | Review Copilot capacity assignment and user scope where used. The data-agent page also uses **Capacities can be designated as Fabric Copilot capacities**; confirm the target portal title. [Admin settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-copilot), [data-agent settings](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-tenant-settings). |
| **Users can access a standalone, cross-item Copilot in Power BI experience (preview)** | Conditional for standalone Copilot / Power BI agent in M365; not a universal substitute for Fabric data-agent publication. Azure OpenAI must also be enabled at tenant level. [Standalone setting](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-copilot#users-can-access-a-standalone-cross-item-copilot-in-power-bi-experience-preview). |

If the selected experience uses OpenAI as a Microsoft subprocessor, separately review
**Users can use Copilot, AI Agents and other AI experiences powered by OpenAI as a
Microsoft Subprocessor** and **Data sent to OpenAI as a Microsoft Subprocessor can
be processed outside your capacity's geographic region, compliance boundary, or
national cloud instance** in the [Copilot settings reference](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-copilot).
These approvals differ from Azure OpenAI. M365/MCP clients can handle responses under
their own terms outside Fabric's boundary. Data-agent settings can take up to an hour
to propagate; a toggle change alone is not evidence of effective principal access.

### Capacity, region, and user entitlements

Plan this workshop on **paid F2 or higher** capacity in each relevant tenant, with
workspaces assigned to active accessible capacity. Microsoft also documents P1-or-
higher Premium capacity with Fabric enabled for data agents. F2 eligibility does
not prove every workload is available or adequately sized; a trial or per-user
Pro/PPU license alone is not the paid-capacity prerequisite for this Copilot path.
Sources: [Copilot enablement](https://learn.microsoft.com/en-us/fabric/fundamentals/copilot-enable-fabric)
and [MCP prerequisites](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-mcp-server#prerequisites).

Check both [Fabric/ontology region availability](https://learn.microsoft.com/en-us/fabric/admin/region-availability)
and [Graph region availability](https://learn.microsoft.com/en-us/fabric/graph/overview#region-availability)
for the **actual capacity region**. These lists are not interchangeable: the checked
Fabric table lists ontology unavailable in South Central US, while the Graph page
lists that region for Graph. No target region is certified by this repository;
availability remains **UNKNOWN until confirmed in the selected tenants**. This is
not an exhaustive license or region matrix.

For M365, confirm the chosen experience's individual licenses, tenant policies,
agent access and source permissions with licensing/admin owners.
[M365 consumption prerequisites](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-microsoft-365-copilot#prerequisites)
list a Microsoft 365 Copilot license or Office 365 commercial subscription, individual
user licenses, and **the same tenant and account** for Fabric and M365. That page
does not certify every Word surface or entitlement combination. Verify the intended
Word client/session separately, rather than declaring all other combinations unsupported.

### Delegated consent is not service-principal RBAC

The [Fabric scopes reference](https://learn.microsoft.com/en-us/rest/api/fabric/articles/scopes)
states that scopes apply to delegated user access. Service-principal access is
controlled by Fabric admin settings and workspace/item permissions. Do not grant
blanket Microsoft Graph application permissions or treat delegated scopes as a
substitute for Fabric RBAC. Fabric `.default` for REST and storage `.default` for
ADLS are different token audiences.

| Delegated operation, when used | Documented scope to review |
|---|---|
| Workspace creation | **Workspace.ReadWrite.All** — [Create Workspace](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/create-workspace). |
| Item creation/definition management | **Item.ReadWrite.All**, or the documented item-specific alternative — [Create Notebook](https://learn.microsoft.com/en-us/rest/api/fabric/notebook/items/create-notebook), [Create Lakehouse](https://learn.microsoft.com/en-us/rest/api/fabric/lakehouse/items/create-lakehouse). |
| Notebook execution | **Item.Execute.All**, or Notebook.Execute.All — [Run On Demand Item Job](https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/run-on-demand-item-job). |
| Shortcut creation | **OneLake.ReadWrite.All** — [Create Shortcut](https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts/create-shortcut); a Fabric REST scope, not the storage SDK audience. |
| Read-only admin settings probe | **Tenant.Read.All** (Tenant.ReadWrite.All is an alternative, not required for a read-only review) — [List Tenant Settings](https://learn.microsoft.com/en-us/rest/api/fabric/admin/tenants/list-tenant-settings). Delegated caller must be a Fabric administrator; supported SP access still needs applicable admin controls. |

These are operation-specific checks, **not a consent bundle for the partner evaluator**.
Its public-client registration uses delegated device login, approved consent, and
least-privilege agent/data access. Do not grant write/admin rights to make it pass.
This repository does not enumerate Entra group membership or install consent grants.

## Read-only preflight: UNKNOWN is not PASS

Preflight acquires tokens, lists accessible active capacities, lists workspaces,
validates explicitly adopted workspace type/capacity, and attempts
`GET /v1/admin/tenantsettings` separately per identity. It never creates resources.
A successful workspace list is not a create-permission test. Capacity Active does
not prove preview availability for its SKU or region.

The documented admin endpoint is
[List Tenant Settings](https://learn.microsoft.com/en-us/rest/api/fabric/admin/tenants/list-tenant-settings).
Its current response collection is `value`; the probe also accepts a single
`tenantSettings` collection for compatibility. Missing, malformed or ambiguous
collections fail closed, and the same pagination URL validation applies to both.
Insufficient admin access or an unavailable endpoint yields **UNKNOWN**, never PASS.
When accessible, exact configured `*_CORE_SETTING_NAMES` are inspected: disabled
settings fail; missing, group-scoped or delegated values stay UNKNOWN. Microsoft Graph
group membership and effective overrides are not inferred or queried.

Each tenant must provide a nonempty `*_ADMIN_ATTESTATION` with an internal review
reference/date for core permissions. Its status is **ATTESTED**, separately from
the unchanged probe result. It never overrides an explicitly disabled configured
setting, certifies a preview, or bypasses sharing/security blockers. Optional setting
lists are not presumed complete. Administrators must review API enablement, workspace
creation, capacity assignment and effective principal scopes; do not guess setting IDs.

Core through-step 4 does not demand accepted shares, ontology/agent IDs or later
security evidence. It validates local manifest and supplied notebook/adapter inputs
before writes. Full mode additionally names known share/security/datasource blockers.

## Data and notebook integration

[data-generator/generators/storage.py](../data-generator/generators/storage.py) defines
the manifest: 18 table Parquets per firm, schema, manifest and local expected-answer
oracle. Step 03 reads listed members only, validates SHA-256, exact firm/table paths,
firm IDs, duplicate members and a complete firm/table matrix. Use immutable generator
output during deployment; do not regenerate concurrently.

Both firms' Parquet, schema and manifest go only to provider Bronze Files. The
multi-firm expected-answer oracle is **not uploaded**. The manifest includes synthetic
configuration and remains provider-only. Never share the raw Files area.

The repository supplies [Bronze-to-Silver](../fabric/notebooks/01_bronze_to_silver.ipynb)
and [Silver-to-Gold](../fabric/notebooks/02_silver_to_gold.ipynb) PySpark templates.
They must validate as nbformat-4 notebooks; their **first code cell** assigns
CONFIG_JSON to a string, normally `CONFIG_JSON = '{}'`. The definition builder adds
the `parameters` tag to that cell in memory if the template does not already have it.
Step 04 AST-parses **every code cell** as pure Python before producing a definition;
invalid later cells and notebook magics fail local validation before cloud writes.
It replaces the parameters code cell **in memory**, serializes configuration as an escaped
Python string literal, strips old outputs/counts and removes stale default-lakehouse
metadata. Template files are never edited. No secrets are injected.

Injected CONFIG_JSON keys:

| Key | Value |
|---|---|
| source_workspace_id, target_workspace_id | Actual provider workspace UUID. |
| source_lakehouse_id, target_lakehouse_id | Actual source/destination UUIDs. |
| source_abfss, target_abfss | Absolute abfss://{workspaceId}@onelake.dfs.fabric.microsoft.com/{lakehouseId} roots. |
| firm_slug | harbor or kestrel. |
| tables | Sorted manifest schema table names. |
| as_of | Manifest snapshot date, never current time. |
| schemas | Schema document's tables mapping, including spark_schema, keys and metadata. |
| ontology | Expanded JSON-compatible contract returned by the implemented local adapter. |

Notebook 01 reads `source_abfss/Files/{firm}/{table}.parquet` and writes Delta to
`target_abfss/Tables/{firm}_{table}`. Notebook 02 reads those firm-prefixed silver tables
and writes `target_abfss/Tables/{table}` in **that firm's separate gold lakehouse**.
Use absolute paths, not an attached lakehouse or ambiguous relative Spark path.
All four firm/stage notebook items run in provider, never against consumer operations.

Step 06 includes the sorted union of manifest schema tables and
`required_gold_tables(ctx.contract())`, including entity, relationship and passthrough
projections. Offline plans load the same local ontology contract and use the raw
generator table list without cloud calls. Manual external invitations must include
this full reviewed table set; inspection still cannot certify their source binding.
No gold folder wildcard or automatic external-share creation is implied.

### Implemented adapter API and its limits

- `fabric.ontology_adapter.load_contract(path) -> dict`: expanded, JSON-serializable contract.
- `build_ontology_definition(contract, workspace_id, lakehouse_id, display_name) -> dict`:
  public definition object containing parts and optional format, **not** a create request.
  Parts have path, InlineBase64 payloadType and payload.
- `build_agent_definition(contract, workspace_id, ontology_id, instructions) -> dict`:
  currently **raises UnsupportedCapability, a ValueError subclass**, because the
  public definition cannot express an ontology source. It never substitutes a raw
  lakehouse or a graph artifact whose ID is actually an ontology ID.

The adapter also implements `relationship_projections`, `required_gold_tables`,
`build_measure_sql`, and `build_agent_instructions`. Its optional
`build_agent_draft_definition` creates only a datasource-free draft shell and is
never a deployment fallback. Portable SQL measures are not native Fabric ontology
measures; an encoded definition is not evidence of service acceptance.

The orchestrator owns names, description markers, state, retries and polling. The
adapter owns version-specific semantics. Envelope validation is not service-side
semantic validation. Live IDs are available via `State.get(resource_key)["id"]`.
Keys include provider.workspace, provider.bronze, provider.silver, provider.gold_harbor,
harbor.workspace, harbor.lakehouse and later harbor.ontology/harbor.agent. In simulation,
`Context.workspace("kestrel")` resolves to harbor.workspace; no third workspace record exists.
Template/contract/instruction paths are independently configurable in the environment example.

## Manual external-share and security obligations

For each firm, the authorized provider user manually selects **only its approved gold
tables**, creates an external invitation and sends it to the intended external tenant
recipient. That recipient accepts into its own consumer lakehouse. Enable creation
and acceptance in the respective tenants first. Never share bronze, silver, multi-firm
Files or local golden answers.

GET shortcut can expose ExternalDataShare and connectionId but not provider workspace,
lakehouse, firm or table binding. Missing shortcuts and wrong target types fail;
all opaque targets present still ends in **EXTERNAL_SOURCE_BINDING_UNVERIFIABLE**.
The inspection function is read-only. Live CLI preflight still blocks this real
sharing path. A manual acceptance or boolean environment variable cannot turn it
into a verified end-to-end deployment.

Before binding confidential raw gold to an ontology/graph, independently validate:

1. Actual delegated identity propagation across the entire query path.
2. Firm/tenant isolation, deny-overrides, default deny and non-inherited parent access.
3. Direct OneLake, SQL and graph access, not merely prompt refusal.
4. Aggregate non-interference, narratives, citations and inferred attributes for the
   screened matter, including the seeded restricted case.
5. Refresh, materialization and caches preserving the same authorization boundary.

The current contract's access relationships do not install RLS. Step 07 fails with
**RAW_GRAPH_SECURITY_VALIDATION_REQUIRED**. No boolean attestation bypass exists.
A future reviewed implementation must supply actual enforcement and verification
before enabling the existing ontology upsert path. Local oracle tests and provider
service-principal job success are not delegated security evidence.

The public agent datasource enum contains graph, not ontology. These IDs are not
interchangeable. Step 08 remains blocked pending a documented supported binding and
an explicit adapter extension. Product-level ontology support in the MCP/M365 guides
does not fill this public-definition gap. Word/M365 publishing, graph refresh and
delegated rehearsal remain separate verification work, never inferred from REST item creation.

## Published agent, MCP evaluation, and Word are different milestones

The [SDK guide](https://learn.microsoft.com/en-us/fabric/data-science/fabric-data-agent-sdk)
separates management from runtime. Publishing a Fabric data agent makes its MCP
endpoint available; the runtime route is
`https://api.fabric.microsoft.com/v1/mcp/workspaces/{WorkspaceId}/dataagents/{DataAgentId}/agent`.
The [MCP protocol flow](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-mcp-server)
requires initialization, tool discovery, and tool invocation, not a guessed REST chat
body. The service supports user and service-principal tokens, but this workshop's
live evaluator intentionally requires **Harbor delegated user identity**.

[agent/evaluate.py](../agent/evaluate.py) executes all **5 canonical + 15 variant**
questions each sweep, three sweeps by default. It checks the Azure-acquired token's
tenant, user object ID, public-client ID, audience, expiry and delegated claims;
claim inspection is not a custom JWT-signature verifier. It never uses a provider
app-token fallback. The immutable user ID must map to Alexandra Reyes's authorized
principal in the enforcing data plane, not just in a prompt.

Authorized responses are JSON **text** containing `answer`, structured `facts`, and
`citations`; MCP `structuredContent` is not silently coerced into an answer. Numeric
comparisons use locally recomputed secure metrics and source IDs. The paragraph
grammar is strict and can reject valid paraphrases. Expected values stay in the
local fixture, not live prompts; follow-ups replay only preceding passed actual
Q1/Q4 responses. Negative probes can name a foreign target but include no foreign
financial or narrative facts. This is a deterministic contract grader, not an
independent model judge or proof that native ontology measures executed.

`--dry-run` validates artifacts/plans without authentication, network or report
writes. `--offline-self-test` generates fixture answers labeled **OFFLINE_NOT_AGENT**;
`--responses-jsonl` labels recordings **RECORDED_NOT_LIVE**. Normal mode is **LIVE_MCP**.
Sanitized JSON/Markdown reports contain provenance hashes, identity checks, timing,
safe failures and response hashes, but no raw answers, tokens, device codes or secret
bodies. Offline reports can have `all_passed=true`. Only actual delegated live runs
can set `three_consecutive_live_rehearsals=true`; **live_readiness stays false even
then**, with `BLOCKED_PREVIEW_RAW_GRAPH_SECURITY_NOT_CERTIFIED` and no portal/Word proof.

After resolving binding/security through an approved implementation, M365 publication
still needs its own supported action. The SDK documentation explicitly says publishing
**to Microsoft 365 Copilot does not use the public API yet**: perform it **inside
Fabric**, either in the portal or with the SDK in a Fabric notebook. The repository's
external CLI does not automate this operation.

Follow [M365 consumption guidance](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-microsoft-365-copilot)
for Agent Store publication, admin extensibility policy, same-tenant/same-account
access, and source permissions. It documents M365/Teams consumption, not proof that
the intended Word client can invoke the agent. Verify Word availability, actual
invocation, citations, refusals and follow-up context separately. Copying a paragraph
into Word is an explicitly manual demonstration, not integrated agent invocation.
M365's orchestrator can rephrase responses even when instructed not to.

## Reliability, ownership, recovery and teardown

- All pagination and Location URLs are validated before attaching tokens: HTTPS,
  exact api.fabric.microsoft.com host, allowed ports, no URL credentials/fragments
  or encoded dot traversal. Redirects are not followed. Storage endpoint is fixed.
- JSON logs contain curated roles/statuses and sanitized UUID request IDs, never
  raw service errors, HTTP bodies or Authorization headers. SDK/httpx logs are suppressed.
- Each selected step emits `step_started` and `step_finished` events with its number,
  dry-run flag, monotonic elapsed seconds, final status and a sanitized error field.
  Failed steps are re-raised and never recorded as completed.
- GET/HEAD transient failures and 429 responses have bounded retries. Retry-After is
  honored, including before the first poll. A delay exceeding the budget causes timeout,
  not early polling. Failed, Cancelled, Deduped and unknown statuses propagate failures.
- **No blind POST retry after ambiguous 5xx or transport failure.** LRO operation ID is
  not resource ID; the operation result is fetched. Notebook success requires Completed.
- The default journal uses the existing ignored .state directory, an exclusive local
  lock, flushed/fsynced temporary file and atomic replacement. Identity/topology is bound
  to the journal. Back it up and keep it private. Windows ACLs are inherited; review
  directory permissions instead of relying on POSIX mode bits. Do not run across hosts
  against one deployment or use a network filesystem with unverified locking semantics.
- Intents precede non-idempotent writes. Trusted operation/job Locations and UUID request
  receipts are persisted when available. Crashes, ambiguous results, failed jobs and
  timeouts retain the intent and stop automatic resume. **Timeout does not cancel a job.**
  An administrator must inspect the receipt, wait/cancel as appropriate, verify resource
  identity/type/marker and reconcile a backed-up journal. Never clear pending entries
  simply to replay creates. No force-recovery option is supplied.
- Created resources have deployment/resource description markers. Saved IDs are checked
  against current type/name/marker. Same-name existing items require the matching marker
  and type before modification, but are always recorded **created=false**. A marker
  never upgrades pre-existing creation ownership.
- Explicit workspace-ID adoption checks type/capacity without changing metadata or
  retagging the workspace. New demo items inside it are individually tagged/tracked.
  Unmarked name-only collisions fail closed.
- Existing shortcuts are compared exactly; conflicts are not overwritten. Only newly
  created shortcuts are journaled as owned. Do not store unrelated files/tables in a
  marked demo lakehouse: ownership/deletion of a lakehouse is item-granular.
- Teardown deletes recorded creations only, validates markers/targets, and removes
  children first. Adopted resources stay. A created workspace containing **any remaining
  items** is preserved, including non-demo artifacts or lagging automatic SQL endpoints.
  It never cascades into those artifacts. Review teardown dry-run before live deletion.
- Teardown is not transactional. It does not automatically revoke manual external shares
  or grants, stop all notebook sessions, or delete/suspend capacity. Revoke shares/grants,
  stop sessions and manage capacity separately under change controls. **Capacity can keep
  billing after teardown.**

## Verified API references and remaining checks

- [Create Workspace](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/create-workspace)
  and [List Capacities](https://learn.microsoft.com/en-us/rest/api/fabric/core/capacities/list-capacities):
  capacityId is supported on create; caller needs capacity/tenant permissions.
- [Create Lakehouse](https://learn.microsoft.com/en-us/rest/api/fabric/lakehouse/items/create-lakehouse):
  field is **creationPayload**, not creationParams. enableSchemas only accepts true.
  Omit it for this deployment and reject schema-enabled reuse to preserve Tables/table.
- [Create Notebook](https://learn.microsoft.com/en-us/rest/api/fabric/notebook/items/create-notebook)
  and [Update Item Definition](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/update-item-definition):
  ipynb public definitions and InlineBase64; updateDefinition takes a definition envelope.
- [Run On Demand Item Job](https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/run-on-demand-item-job)
  and [Get Item Job Instance](https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/get-item-job-instance):
  POST /v1/workspaces/{ws}/items/{id}/jobs/RunNotebook/instances, then poll returned Location.
  executionData is item-specific; generic parameters are not broadly supported. This
  implementation injects CONFIG_JSON in the definition and submits **no executionData
  or parameters body**, rather than guessing a notebook parameter envelope.
- [Notebook public APIs](https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-public-api)
  confirms service-principal execution. Its illustrative execute/beta routes differ
  from the core scheduler reference; implementation follows the stable generic reference
  and server Location, not an illustrative beta route.
- [Get Operation State](https://learn.microsoft.com/en-us/rest/api/fabric/core/long-running-operations/get-operation-state):
  poll status then result; same-host /operations/ Locations are accepted because some
  examples omit /v1 in returned operation URLs.
- [List Tenant Settings](https://learn.microsoft.com/en-us/rest/api/fabric/admin/tenants/list-tenant-settings)
  and [OneLake Python access](https://learn.microsoft.com/en-us/fabric/onelake/onelake-access-python).
- [Create Shortcut](https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts/create-shortcut)
  and [Get Shortcut](https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts/get-shortcut):
  Tables/name with target.oneLake workspaceId, itemId and Tables/table path. ExternalDataShare
  is readable but not a documented creatable target; source binding is opaque.
- [External sharing](https://learn.microsoft.com/en-us/fabric/governance/external-data-sharing-overview):
  recipient chooses a lakehouse; provider governance does not automatically transfer.
- [Create Ontology](https://learn.microsoft.com/en-us/rest/api/fabric/ontology/items/create-ontology),
  [Create Data Agent](https://learn.microsoft.com/en-us/rest/api/fabric/dataagent/items/create-data-agent),
  [Ontology definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/ontology-definition),
  [Data Agent definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/data-agent-definition):
  create envelopes contain displayName, description and definition parts. Logical
  ontology IDs are positive 64-bit values; LakehouseTable bindings and relationship
  contextualizations use physical workspace/item IDs. No native measure DSL, invented
  relationship attributes, or ontology datasource discriminator is emitted.
- Discovery/adoption: [List Workspaces](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/list-workspaces),
  [Get Workspace](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/get-workspace),
  [List Items](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/list-items),
  [Get Item](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/get-item), and
  [Get Lakehouse](https://learn.microsoft.com/en-us/rest/api/fabric/lakehouse/items/get-lakehouse).
  Inspect type, name, ownership marker and capacity/schema before reuse. A list
  operation does not prove permission to create or delete.
- Completion: [Get Operation Result](https://learn.microsoft.com/en-us/rest/api/fabric/core/long-running-operations/get-operation-result)
  retrieves the actual created resource after a successful LRO; an operation ID is
  never treated as an item ID.
- Teardown: [Delete Shortcut](https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts/delete-shortcut),
  [Delete Item](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/delete-item), and
  [Delete Workspace](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/delete-workspace).
  Local ownership/remaining-item checks further restrict these destructive APIs.
  Workspace deletion requires Admin and would cascade without those local checks.
  Shortcut deletion leaves destination storage intact. The current generic item API
  defaults to soft deletion for supporting item types; permanent deletion requires
  an explicit hardDelete request and Admin. This repository does not request hardDelete:
  inspect retention, item-type/SP support and residual storage rather than claiming
  permanent erasure or zero cost after a successful DELETE.
- ADLS upload: [OneLake API access](https://learn.microsoft.com/en-us/fabric/onelake/onelake-access-api)
  and [DataLakeFileClient.upload_data](https://learn.microsoft.com/en-us/python/api/azure-storage-file-datalake/azure.storage.filedatalake.datalakefileclient#azure-storage-filedatalake-datalakefileclient-upload-data).
  Uses the provider storage token and fixed OneLake endpoint; storage success is
  not evidence of consumer or delegated-user authorization.
- Refresh and consumption: [Ontology overview](https://learn.microsoft.com/en-us/fabric/iq/ontology/overview),
  [Graph overview](https://learn.microsoft.com/en-us/fabric/graph/overview),
  [MCP runtime](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-mcp-server),
  [SDK publication](https://learn.microsoft.com/en-us/fabric/data-science/fabric-data-agent-sdk), and
  [M365 consumption](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-microsoft-365-copilot).
  Upstream changes require graph refresh; no refresh automation or Word-publication
  REST contract is inferred from item creation.

## Verify before running

Three source-level `# VERIFY:` markers identify tenant-dependent contracts:
**NOTEBOOK_JOB_V1** in [fabric_client.py](../fabric/fabric_client.py), and
**ONTOLOGY_V1** and **AGENT_ONTOLOGY** in
[ontology_adapter.py](../fabric/ontology_adapter.py). They cover Spark execution,
preview property/binding types and refresh, and the missing documented agent source
discriminator. The additional hard blockers and validation obligations are listed
below. Rerun the marker scan after adapter changes; preview product support is not
proof of a specific REST payload.

| Check | Implementation / required evidence |
|---|---|
| **VERIFY: topology and identities** | [fabric/config.py](../fabric/config.py): three tenants by default; explicit 2-tenant/3-workspace or 1-tenant/2-workspace alternatives. Confirm separate token clients, principal ownership, consumer permissions, and actual user-to-domain identity mapping. |
| **VERIFY: tenant switches and admin visibility** | [fabric/deploy.py](../fabric/deploy.py): verify every applicable display-name switch above, effective groups/overrides, exact internal setting names, and per-tenant attestations. UNKNOWN is not PASS; ATTESTED does not override a disabled setting. |
| **VERIFY: capacity, region and entitlements** | Confirm paid F2+ or documented alternative, active capacity assignment rights, actual ontology and Graph availability, AI-region/storage approval, external-client terms, and target M365/Word licensing. No cloud region or license combination is certified locally. |
| **VERIFY: source/schema and notebooks** | [step03_upload.py](../fabric/steps/step03_upload.py), [step04_notebooks.py](../fabric/steps/step04_notebooks.py), and [ontology_adapter.py](../fabric/ontology_adapter.py): freeze checksummed output; validate all nbformat/AST cells, source fields, aliases, edge tables and exact cents before writes. Execute Spark separately and inspect persisted counts/invariants. Static parsing is not execution. |
| **VERIFY: core REST and storage contracts** | [fabric_client.py](../fabric/fabric_client.py) and [steps/common.py](../fabric/steps/common.py): recheck create/read/update/delete envelopes, supported SP identities, storage permissions, scheduler route, LRO result, trusted Locations and Retry-After behavior against the references above. |
| **VERIFY: external share creation and acceptance** | [step05_external_share.py](../fabric/steps/step05_external_share.py): `EXTERNAL_SHARE_MANUAL`; authorized provider creates an invitation for only one firm's reviewed Gold table set and the intended external recipient accepts. There is no verified public create/accept contract or copy fallback. |
| **VERIFY: exact external source binding** | [step06_shortcuts.py](../fabric/steps/step06_shortcuts.py): missing targets raise `ACCEPTED_EXTERNAL_SHORTCUT_MISSING`, wrong target types raise `EXPECTED_EXTERNAL_SHARE_TARGET`, and opaque accepted targets still raise `EXTERNAL_SOURCE_BINDING_UNVERIFIABLE`. A connectionId is not proof of provider/firm/table provenance. |
| **VERIFY: raw graph security before binding** | [steps/common.py](../fabric/steps/common.py), [step07_ontology.py](../fabric/steps/step07_ontology.py): `RAW_GRAPH_SECURITY_VALIDATION_REQUIRED`; actual deny-overrides, direct-engine access, delegated propagation, aggregates/narratives/citations, refresh and caches require an enforcing implementation plus independent tests. No environment attestation bypass. |
| **VERIFY: supported ontology agent binding** | [ontology_adapter.py](../fabric/ontology_adapter.py), [step08_agent.py](../fabric/steps/step08_agent.py): `ONTOLOGY_DATASOURCE_UNSUPPORTED`; do not substitute an ontology ID for a graph ID. A datasource-free draft helper is not a grounded agent. Obtain a supported contract before extending the adapter. |
| **VERIFY: graph refresh and snapshot** | Confirm the materialized graph reflects the selected Gold snapshot and preserves authorization after refresh. A live-read shortcut does not make graph materialization automatically current. No refresh controller is implemented. |
| **VERIFY: delegated evaluation and metric semantics** | [agent/runtime.py](../agent/runtime.py), [agent/evaluation.py](../agent/evaluation.py), [agent/evaluate.py](../agent/evaluate.py): all 5+15 probes, strict JSON-text and paragraph contract, actual delegated identity, secure currency/cohort metrics, response provenance and failed/error cases. Fixture success is not independent agent evidence. |
| **VERIFY: publication and Word proof** | Fabric publication, M365 Agent Store publication from inside Fabric, same-tenant/same-account access and actual Word invocation are separate. `BLOCKED_PREVIEW_RAW_GRAPH_SECURITY_NOT_CERTIFIED`, `live_readiness=false` and absent portal/Word proof remain honest even when answer grading passes. |
| **VERIFY: recovery, teardown and residual cost** | [fabric/state.py](../fabric/state.py), [fabric/deploy.py](../fabric/deploy.py): reconcile pending operations without blind replay; review owned-resource deletion and adopted-workspace protection. Manually revoke shares/grants, stop sessions and manage capacity; teardown does not stop all billing. |

[tests/test_fabric_client.py](../tests/test_fabric_client.py) and
[tests/test_fabric_deployment.py](../tests/test_fabric_deployment.py) cover trusted hosts,
pagination, retries, deadlines, LRO/job failures, ambiguous writes, ownership, teardown,
dry-run isolation, topology, manifest integrity, notebook injection, preflight and
security gates with MockTransport. Clean editor diagnostics alone are not a passing
test suite or live deployment certificate. See [repository checks](../tests/test_repository.py)
for file, notebook, diagram, source-projection, and documentation consistency checks;
these do not execute Spark or certify a tenant.