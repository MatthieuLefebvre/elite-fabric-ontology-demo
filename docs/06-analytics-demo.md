# Firm analytics: live status and remaining setup

Status verified on 2026-09-08. This is the explicitly approved **firm-wide synthetic
administrator demo**, not a partner-secured deployment. Existing workspace access
and sharing were not changed. Items inherit workspace access; their names and
descriptions are not access controls.

## Live artifacts

All four artifacts are in an approved folder in the existing provider workspace.
Each semantic model reads only its own populated provider Gold lakehouse through
Direct Lake on SQL. The consumer lakehouses are not used by these reports.

| Firm | Semantic model | Report |
| --- | --- | --- |
| Harbor | Harbor Finance Model | Harbor Finance Insights |
| Kestrel | Kestrel Finance Model | Kestrel Finance Insights |

Each report has Financial Overview, WIP & Collections, Matter Performance, and a
hidden Matter Detail drillthrough page. Native visuals include KPI cards, issue-date
trends, practice comparisons, WIP aging, stage/reason reductions, budget comparisons,
and matter/invoice tables. Currency, practice, client, and matter slicers are included.
The default currency is USD for both firms; GBP remains separately selectable.
Each slicer has a distinct synchronization group spanning all four pages of its
report, including Matter Detail. Currency, practice, client, and matter selections
are configured to persist when navigating between pages. Groups are report-specific.

The snapshot is 2026-09-04. Money measures return blank for mixed or blank currencies.
WIP includes draft-allocated work until invoiced. Realization uses issued net time
fees divided by the same cohort's standard value. Cash and invoice write-offs are
matched to that cohort. Quarter actuals match budget matter, phase, and currency;
the full-quarter plan is not prorated. These are firm-wide measures without partner RLS.

## Verification

- Both models accepted the definitions and executed live DAX queries.
- Live fees, bills, WIP, cash, receivables, quarter plans, and quarter actuals matched
  independent integer-cent calculations over the generated Parquet, for both firms
  and both currencies.
- All 58 PBIR parts per report passed Microsoft's published JSON schemas. Local
  tests check visual bounds, non-overlap, native visual types, and model field references.
- The Power BI API returned all four published pages for both reports.
- Report-loading investigation: the user reported blank/endless loading in managed
  Edge. A live definition update exposed `Workload_MissingFileFromDefinition`:
  the imported custom-theme resource path omitted `.json`, while the uploaded file
  retained it. Theme names, resource paths, and filenames now match for both firms;
  successful updates and subsequent definition exports verified the repair. Pages,
  visuals, model bindings, and sharing were preserved. The failed update receipt is
  retained under `failed_analytics_report_updates`; no write intents remain pending.
  Harbor still loaded blank immediately after that theme-path repair.
  The missing theme was a confirmed defect, not a complete explanation of the loading
  failure. A complete Microsoft-sample base-theme probe also remained blank and was
  removed; its previous definition and outcome are retained in `analytics_report_probes`.
  The explicit `/overview?experience=power-bi` route displayed the Fabric toolbar but
  not the canvas. A server-side PDF export was accepted but remained Running at 0%
  for more than eight minutes; its ID and last observed status are retained in
  `analytics_report_render_diagnostic`. This is not successful rendering evidence.
  Subsequent isolated version and textbox probes also remained blank.
- A later session applied startup metadata from the tenant-authored Harbor Diagnostic
  report: internal definition version `2.0.0`, its report settings, and the
  `Fluent2-CY26SU09` base theme and shared resources. Harbor's custom theme and all
  pages were retained. The user's next screenshot showed the report rendering,
  with clipped slicers and blank KPI values. A full generated-definition update for
  slicer spacing then overwrote that startup patch; reverting the slicer changes
  alone did not restore rendering. The tenant-authored startup patch has now been
  reapplied to Harbor, with all four pages and 49 visuals verified unchanged by
  definition read-back. **The user confirmed rendering after this restoration.**
  Existing-report deployments now preserve deployed startup metadata and static
  resources instead of replacing them with generator defaults. New-report defaults
  remain unverified for rendering. A subsequent Harbor slicer-only update hid the
  duplicate field headers and set all slicers to 70 pixels tall. Live read-back
  verified exactly 16 slicer definitions changed and all 44 other report parts
  stayed unchanged, including startup metadata, themes, and model binding. The
  working baseline is backed up in `analytics_report_slicer_updates`; browser
  confirmation of the corrected dropdown layout is pending. Blank KPI values
  remain unresolved. Harbor Diagnostic was read only.
- Kestrel subsequently received Harbor's working startup metadata and shared base
  theme while retaining its own model binding, custom theme, and all 49 visuals.
  Its 16 slicers received the same height/header correction. Both reports now have
  four native `visual.syncGroup` groups with `filterChanges` and `fieldChanges`
  enabled, each joining the matching slicer on all four pages. Live read-back
  verified every published part: 19 changed parts for Kestrel, 16 slicer-only changes
  for Harbor, and no pending writes. Before/after definitions are journaled in
  `analytics_report_slicer_updates`. Kestrel rendering and cross-page selection
  behavior in both reports still require managed-browser confirmation.
- **Rendering and interactive behavior are not visually certified.** Embedded-browser
  sign-in was denied by device-compliance policy (53000). Image export returned 403:
  `Export report to image is disabled on tenant level`. No policy was bypassed.
  Review both reports in a managed browser, including slicers, drillthrough, and mobile.

## Repeatable commands

Run from the repository root with the existing Azure CLI user sign-in:

```powershell
./.venv/Scripts/python.exe -m fabric.deploy_analytics --config .env --administrator-synthetic-demo --stage models
./.venv/Scripts/python.exe -m fabric.deploy_analytics --config .env --administrator-synthetic-demo --stage reports
```

Use `--firm harbor` or `--firm kestrel` to limit an operation. This separate command
does not rerun Spark, alter sharing, certify partner security, or mark steps 5-8
complete. It retains the existing ownership journal and pending-write safeguards.

## Live ontologies and data agents

The user approved an alternate destination workspace and folder on a different
West Central US capacity. The workspace is adopted, not demo-owned. Its identifiers
remain in the ignored local state journal rather than public documentation. The
destination is saved in `analytics_workspace_assignment`; ontology deployments use
it while Gold bindings and report links still point to the original provider workspace.

The user resumed the destination capacity. Both ontologies are now deployed and
queryable, and both ontology-bound Data Agents exist. Harbor is published and its
authenticated MCP endpoint was exercised successfully on 2026-09-07. Kestrel has
only draft-definition evidence in this repository and must not be described as
published until its MCP endpoint is tested.

| Firm | Ontology | Data agent | Runtime status | Entity instances | Relationships |
| --- | --- | --- | --- | ---: | ---: |
| Harbor | Harbor Legal Ontology | Harbor Legal Ontology Agent | Published; MCP query verified | 23,550 | 56,720 |
| Kestrel | Kestrel Legal Ontology | Kestrel Legal Ontology Agent | Draft definition verified | 7,173 | 16,856 |

Live definition exports verified 14 entity types, 29 relationship types, 14 Gold
bindings, and 14 matching report links in each ontology. GQL counts for all 14 entity
types matched their local source Parquet row counts in both firms. A directed
relationship traversal returned the totals above; individual edge correctness and
financial answers have not yet been evaluated. Kestrel's initial refresh was
Cancelled; one explicit refresh completed at 2026-09-08T00:59:43Z before these checks.
Both agents select all 14 entities from their own ontology, in the approved folder.
Verification evidence is in `analytics_ontology_verification`; no writes remain pending.

Earlier blockers are resolved for this destination: the original provider workspace
hit `WorkspaceItemsLimitExceeded` at 995 visible items, and the destination initially
returned `CapacityNotActive`. Rejected operations were reconciled and archived in
`failed_analytics_provisions`. Source reports, data, and permissions were unchanged.

The compiler now emits documented native `semanticEnrichment` descriptions for
entities, properties, and relationships, with physical Gold bindings. The analytics
stage also adds matching Power BI report resource links. Portable SQL measures are
not native ontology measures. JSON discriminator order is preserved because the live
preview importer requires `sourceType` and `dataBindingType` first. Resource links
likewise require `type` first; sorting that discriminator after `itemId` caused
`ALMOperationImportFailed`. Preserving insertion order fixed the import.

Both failed Harbor imports were reconciled only after their operations were terminal
Failed and their ontology roots returned 404. The service left backing resources;
their IDs and operation evidence are preserved in the private journal's
`failed_analytics_imports`. These unmarked service-generated items were not deleted
or claimed as ordinary demo-owned resources. Review them with the workspace owner
before cleanup; the normal teardown does not own them.

The deployment provisions the empty ontology first, then imports the bound
definition using `updateDefinition`. This separates item provisioning from schema
import and retains a managed root for diagnosis if the second stage fails.
Repeatable commands use the saved destination:

```powershell
./.venv/Scripts/python.exe -m fabric.deploy_analytics --config .env --administrator-synthetic-demo --stage ontologies
./.venv/Scripts/python.exe -m fabric.deploy_analytics --config .env --administrator-synthetic-demo --stage agents
```

The stage requires every entity, bridge, and filtered edge Delta table to be visible
in the live Gold catalog. After creation, refresh the ontology graph and verify
entity instance counts, relationship traversal, descriptions, and report links.
Definition acceptance alone is not evidence that graph ingestion succeeded.

## Use the published Harbor MCP server

Fabric exposes a data agent as an MCP server only after the agent is published.
The endpoint uses MCP over streamable HTTP, not an arbitrary REST chat request, and
every request needs a Fabric bearer token for
`https://api.fabric.microsoft.com/.default`. The caller must be able to access the
workspace, data agent, and underlying ontology data. Client output can leave the
Fabric compliance boundary under the MCP client's data-handling terms.

For Visual Studio Code, create a workspace-local `.vscode/mcp.json` that is excluded
from source control when it contains environment-specific endpoints:

```jsonc
{
  "servers": {
    "Elite-Harbor-MCP": {
      "url": "https://api.fabric.microsoft.com/v1/mcp/workspaces/<WORKSPACE_ID>/dataagents/<DATA_AGENT_ID>/agent",
      "type": "http"
    }
  },
  "inputs": []
}
```

Start the server from VS Code, approve interactive authentication, and sign in with
an authorized account. In agent mode, ask a bounded question such as `Give me the
matter details for Ashworth`. On 2026-09-07 this endpoint completed the MCP handshake,
advertised the Harbor ontology tool, and returned four matching Ashworth matters.
That proves MCP runtime availability for the testing identity; it does not prove
partner-level authorization, Microsoft 365 publication, or Word behavior.

Copy the exact URL from the published agent's **Settings → Model Context Protocol**
page. The public endpoint documented by Microsoft uses `api.fabric.microsoft.com`;
internal or sovereign environments can use a different host, which must not be
silently substituted. See Microsoft's
[MCP server guidance](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-mcp-server)
for the protocol, authentication, and client requirements.

## Publish to Microsoft 365 Copilot and verify Word

Word does not read VS Code's `.vscode/mcp.json`. For the supported Fabric route,
open the validated data agent in Fabric, publish it, and select **Publish to Agent
Store**. Microsoft documents Microsoft 365 publication from the Fabric portal or by
running the Fabric Data Agent SDK in a Fabric notebook; that operation is not yet
available through the public API outside Fabric. The Fabric data agent and Microsoft
365 Copilot must use the same tenant and account. Each user also needs the applicable
Microsoft 365 or Office 365 entitlement, access to the agent, and access to every
underlying source. An administrator might need to enable Copilot extensibility.

After the agent appears in the Microsoft 365 Agent Store, add it and test it in the
managed Word client with the same signed-in account. Record the client/version,
identity, prompt, answer, citations, and effective data permissions. Agent Store
visibility or a successful Teams chat alone is not Word evidence. Harbor has not yet
completed this Word verification, and Kestrel has neither MCP nor Word runtime proof.
See Microsoft's
[Microsoft 365 consumption guidance](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-microsoft-365-copilot)
and [Data Agent SDK publication note](https://learn.microsoft.com/en-us/fabric/data-science/fabric-data-agent-sdk#publish-the-data-agent).

## Test and publish the remaining agent

The current [product documentation](https://learn.microsoft.com/en-us/fabric/data-science/concept-data-agent)
lists ontologies as supported data sources. The public
[Data Agent definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/data-agent-definition)
still omits an ontology datasource discriminator. A read-only export of an existing
tenant-authored ontology agent established the preview format: datasource type
`ontology`, the actual Ontology item ID, and name-based `ontology.entity` selections.
The new builder uses that observed format, not a GraphModel or semantic-model
substitute. Successful draft creation and definition read-back establish support in
this tenant, not a universally documented public contract.

Open Kestrel in the managed Fabric portal and test before publishing. Both agents
include explicit instructions for synthetic firm-wide scope, snapshot, currency,
integer cents, WIP, issued-cohort realization, collections, and budget grain. They
must acknowledge unavailable calculations rather than invent ready-made metrics.
The separate secured deployment remains fail-closed; these administrator agents do
not certify partner authorization or complete secured steps 5-8.

Test at least one relationship-traversal question and one currency-specific financial
question against known values. An unavailable metric should be acknowledged, not
guessed. Harbor has one successful exploratory MCP query, not the full delegated
evaluation suite. Kestrel's live natural-language answers have not been evaluated.