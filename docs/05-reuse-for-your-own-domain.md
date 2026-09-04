# Reuse for your own domain

Reuse the separation between **source data**, **business meaning**, **physical
bindings**, and **authorization**, rather than the legal nouns alone. The repository
is a legal-finance workshop with reusable deployment mechanics, not an arbitrary-
domain generator or a security platform. A new domain still needs real source
schemas, deterministic scenarios, accounting/operational invariants, and tested
enforcement before an agent can safely consume it.

## Start with one decision and its evidence

For a field-service business, a useful first question is: “Which service contracts
are over their labor budget, and did value disappear during work approval or after
invoicing?” Agree with the business owner on what counts as work, approval, billing,
cash, and a concession. Decide whether the denominator is a quoted job, completed
visits, invoiced labor, or a service period. A renamed legal ratio is not automatically
a valid service KPI.

Use a fixed snapshot and a small deliberately planted scenario: one unapproved visit,
one over-budget contract, one pre-invoice concession, one post-invoice credit, one
territory-restricted contract, and one foreign-currency contract. Calculate expected
answers from rows, including empty-denominator and partial-payment cases. Do not use
those expected answers as examples in the agent's prompt.

## Worked field-service mapping

| Legal concept | Field-service concept | Required design decision |
|---|---|---|
| Client | Customer | Account ownership is distinct from contract access. |
| Matter | ServiceContract | Decide whether individual WorkOrders need a separate entity rather than using parent/child contracts. |
| Timekeeper | Technician | Keep technician, dispatcher, and account-manager roles distinct. |
| PracticeGroup | ServiceTeam | Team membership is not automatically an authorization grant. |
| LegalEntity | OperatingEntity | Preserve jurisdiction and functional currency. |
| TimeEntry | TechnicianVisit | Decide whether one row is a visit or a labor line; retain duration, rates, work date, and currency. |
| Disbursement | PartOrTravelCharge | Separate labor realization from parts and travel recoverability. |
| Proforma | ServiceApproval | A pre-invoice review, not another invoice after conversion. |
| Invoice / Payment | Invoice / CashApplication | Keep issued values and one application per invoice; handle partial payment explicitly. |
| Adjustment | Concession | Distinguish visit correction, approval discount, credit note, and bad debt; a legal write-off is not a complete credit-note ledger. |
| Budget | ContractBudget | Align actuals and plan by contract, phase, period, currency, and hours/fees. |
| BillingGuideline | ServiceBillingRule | Define units and effective dates; an SLA rule may require different fields and logic. |
| MatterAccess | ContractAccess | Enforce territory/account restrictions, explicit exclusions, and non-inherited child access. |

This **illustrative entity YAML sketch** uses the existing portable entity shape;
it is not a complete runnable contract, a generator configuration, or a Fabric REST
payload. The referenced `service_contracts` source must be implemented first.

```yaml
name: ServiceContract
description: >-
  A customer service agreement with a labor budget and its own access boundary.
  Work-order access is evaluated separately, not inherited from this contract.
table: service_contracts
key: contract_id
display_name: name
properties:
  contract_id: {column: contract_id, type: string}
  firm_id: {column: firm_id, type: string}
  name: {column: name, source_column: contract_name, type: string}
  customer_id: {column: customer_id, type: string}
  currency: {column: currency, type: string}
  labor_budget_cents: {column: labor_budget_cents, type: long}
  labor_budget:
    column: labor_budget
    type: 'decimal(18,2)'
    expression: 'CAST(labor_budget_cents AS DECIMAL(38,2)) / 100'
```

Here `firm_id` is retained as the current pipeline's isolation key, not a requirement
that a service business be a law firm. Changing it to `organization_id` requires
coordinated schema, validation, notebook, measure, and security changes. All property
expressions evaluate against the **original source row**, not newly projected aliases.
The adapter normalizes `source_column` to an expression and retains exact cents.
The graph's Double projection of a decimal remains unsuitable for exact reconciliation.

Add Customer and TechnicianVisit entities, a visit-to-contract relationship with
explicit source/target key columns, and any approval-to-invoice bridge. Filtered
relationships require actual Gold edge tables; the public ontology contextualization
does not accept an arbitrary SQL filter. Extend the root contract references, measure
context, and security SQL together. The entity sketch alone is deliberately insufficient
for `load_contract()` to validate a whole domain.

## What can change through configuration today

Within the **existing legal schema**, [firm configuration](../data-generator/config/firms.yaml)
controls the two firms' names, sizes, first partners, authored customer/matter/person
names, cash lags, WIP volumes, and planted leakage percentages.
[Simulation configuration](../data-generator/config/simulation.yaml) controls seed,
snapshot, rates, concessions, and budget baselines; the
[client/matter configuration](../data-generator/config/clients_matters.yaml) controls
the legal scenarios and relationships. Keep `harbor` and `kestrel` as the current
deployment role slugs unless you also update the orchestration contract.

Changing rates or names can demonstrate a different firm without changing the
source schema. It cannot generate TechnicianVisit, WorkOrder, machine telemetry,
inventory movements, or arbitrary domain fields. For field service, replace or
extend [source schemas](../data-generator/generators/common.py), generators, expected-
answer calculations, validation, and the serialized schema/manifest contract. Preserve
the same-firm keys, deterministic RNG, explicit date boundaries, and checksummed
artifact discipline. Never synthesize missing production facts as convenient Gold
constants just to make an ontology binding succeed.

## Where changes belong

| Layer | Keep or adapt |
|---|---|
| REST transport and lifecycle | Reuse [fabric_client.py](../fabric/fabric_client.py), ownership journal, bounded retries, LRO/job polling, and teardown protections; reverify each new item API and identity model. |
| Deployment configuration | Keep separate tenant clients and private credentials. The current uploader/table allowlist and two-firm topology are legal-demo-specific, so arbitrary schemas or extra customers require explicit changes. |
| Bronze/Silver/Gold | Reuse explicit schemas and original-row projection principles, not an assumption that current notebook validation is domain-neutral. Review every legal FK and financial invariant. |
| Ontology | Replace entity/relationship semantics, key mappings, measure SQL, and security context in [fabric/ontology](../fabric/ontology/ontology.yaml). Logical renames change stable IDs and require migration. |
| Public adapter | Reuse supported encoding where shapes match. Keep version-sensitive REST translation in [ontology_adapter.py](../fabric/ontology_adapter.py); never put guessed service fields in domain YAML. |
| Agent and evaluation | Replace [instructions](../agent/agent_instructions.md), [questions](../agent/example_questions.jsonl), golden calculations, structured facts, citations, and strict paragraph grammar. The current evaluator is Harbor-specific, not a generic domain judge. |

Do not casually carry over `days_sales_outstanding`: this ontology defines an
**unweighted invoice-to-cash-application delay**, not standard sales DSO. Field service
might instead need work-completion-to-cash or invoice aging; give it a distinct name
and observation grain. For realization, approval concessions may reduce issued labor
fees; post-invoice bad debt affects recoverability, while tax credits, refunds, and
reversals need additional ledger semantics not modeled here. Never add currencies
or average ratios across unequal denominators.

## Portability does not remove the security gate

Map the real delegated Entra identity to the domain principal in a trusted execution
context. For the worked case, authorize ServiceContract and WorkOrder separately;
subtract exclusions from grants before aggregating labor, parts, narratives, and
citations. Territory adjacency in a graph is not permission. A service principal
with all-territory access cannot impersonate a technician merely by changing a prompt.

Before binding confidential source tables, prove direct OneLake/SQL/graph access,
shortcut behavior, cache and refresh behavior, denial-overrides, and aggregate
non-interference in the target tenant. Retain the current hard binding block until
a reviewed enforcing implementation and evidence exist. If residency requires source
data to stay customer-side, move ingestion there; provider synthetic sharing does
not already satisfy that requirement.

## A practical acceptance sequence

First, approve the domain vocabulary and one reconciled decision scenario. Then
generate deterministic fixtures and validate primary keys, foreign keys, currency,
chronology, stage attribution, and access-denial invariants. Compile the portable
contract and check every projected column and relationship table against the schema.
Validate both notebooks as nbformat and Python AST before a separately authorized
Spark run. Regenerate expected answers rather than copying the legal demo card.

Next, review the [deployment prerequisites and VERIFY list](03-deployment-guide.md#verify-before-running)
for each tenant. Prepare only supported core resources, and independently resolve
share acceptance, source binding, security enforcement, and supported agent datasource
binding. A documented UI feature is not proof of a public REST contract. An offline
fixture pass is not an independent agent evaluation; keep fixture, recorded, and live
provenance distinct and require all security probes.

Finally, after an actual supported agent is published, test its delegated MCP runtime
and separately publish to M365 from within Fabric where supported. Confirm target
application availability, same-account access, citations, and restrictions in Word;
an Agent Store listing alone is not full integration evidence. Rehearse failure and
teardown as well as the positive story. No domain rename changes these obligations.