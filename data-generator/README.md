# Deterministic synthetic legal-finance source

Python 3.11+, PyYAML, PyArrow, and pytest are sufficient. Dependencies are already
declared by the parent repository; this directory does not manage environments.
All implementation and tests are local. No cloud calls, agent simulation, external
name corpus, wall clock, independent RNGs, or secrets are used.

## Entry points

[generate.py](generate.py) exposes:

- `load_config(directory=DEFAULT_CONFIG)`: load and validate the three YAML files.
- `generate(seed=None, as_of=None, config=None) -> Dataset`: generate, validate,
  and calculate golden facts in memory. One `random.Random(seed)` is passed into
  activity generation. The caller's config and global RNG state are not mutated.
- `write_dataset(dataset, output="data") -> dict`: serialize source files and metadata.
- `load_dataset(output) -> Dataset`: verify manifest content hashes and load Parquet.
- `schema_document() -> dict`: the complete Spark-compatible schema contract.

CLI options are `--seed`, `--as-of`, `--output`, `--config`, and `--dry-run`.
Defaults are seed **42**, fixed snapshot **2026-09-04**, and output directory
**data relative to the caller's working directory**. No today-based fallback exists.
Dry-run performs full generation, invariants, and golden calculations but creates
no directories or files; its JSON stdout includes counts and golden facts.

The configurable service window is inclusive, from the snapshot minus 18 calendar
months through the snapshot: **2025-03-04 through 2026-09-04** by default. Budget
periods and monthly cohorts use half-open intervals. Budget actuals are current
quarter to snapshot, compared against the full-current-quarter budget, without
proration. Moving the snapshot regenerates the entire scenario, including the
planted current-quarter and prior-month cases; it does not replay a historical ledger.

## Output and schema

Each output firm subdirectory, **harbor** and **kestrel**, contains one Parquet file
for each of these 18 tables. Each root run also writes manifest JSON, schema JSON,
and expected-answer JSON. Their names are `manifest.json`, `schema.json`, and
`expected_answers.json` respectively. Source schemas are defined by `SPECS` in
[generators/common.py](generators/common.py); output metadata is implemented in
[generators/storage.py](generators/storage.py).

| Table | Primary key | Grain and important relationships |
|---|---|---|
| firms | firm_id | Tenant/firm, with stable slug |
| offices | office_id | US and UK offices per firm |
| practice_groups | practice_group_id | Corporate, litigation, IP, employment, real estate |
| legal_entities | legal_entity_id | Office plus operating currency |
| timekeepers | timekeeper_id | Firm, office, entity, practice, role, standard rate/currency |
| clients | client_id | Fictional client within a firm |
| matters | matter_id | Client, entity, practice, three distinct partner roles, optional parent |
| rates | rate_id | Matter/timekeeper negotiated rate with inclusive validity dates |
| time_entries | time_entry_id | Decimal hours, standard and negotiated cents, optional billing links |
| disbursements | disbursement_id | Cost cents with optional billing links |
| proformas | proforma_id | Draft/converted fee-and-cost document, optional invoice |
| invoices | invoice_id | Issued document and its unique originating proforma |
| payments | payment_id | Positive cash against a single invoice |
| adjustments | adjustment_id | One positive reduction, one stage, one target, unique provenance |
| budgets | budget_id | Single matter/current-quarter fee and hour baseline |
| billing_guidelines | billing_guideline_id | Client/currency terms and optional fee cap |
| invoice_guidelines | invoice_guideline_id | Invoice-to-applicable-client-guideline link |
| matter_access | matter_access_id | Explicit timekeeper/matter grant or deny |

All tables carry `firm_id`. Every entity ID is a string prefixed by the firm slug;
IDs do not depend on seed. Examples: `harbor_f001`, `harbor_t001`, `harbor_c001`,
**`harbor_m001`**, `harbor_te001`. Matter IDs use `m` plus at least three decimal
digits, so the default cases are `harbor_m001` through `harbor_m012` and the same
suffixes under `kestrel`. The first partner is Alexandra Reyes in Harbor and Iona
Vale in Kestrel. Responsible, billing, and originating partner IDs are separate and
distinct; role assignment does not confer access. Three seeded parent/child pairs
exist per firm: 1→2, 4→11, and 6→12. Q1/Q4 exclude child rollups explicitly.

Kestrel has separately authored client, matter, and timekeeper names, not Harbor
names with a different ID prefix. For example, its first and tenth matters belong
to Asterwick Engineering and Highmere Asset Management. Practice categories and
physical office cities remain shared vocabulary, not shared business identities.

The schema JSON contains `tables[table].spark_schema`, directly compatible with
Spark `StructType.fromJson`, plus primary/FK metadata and money definitions. Arrow
schemas are explicit even for empty or all-null columns. Types are `string`, `long`
(money cents), `date`, `decimal(10,2)` (hours), `decimal(18,2)` (guideline thresholds),
and `boolean`; nullability is explicit. Logical type `M` maps to Arrow decimal128(18,2)
and Spark decimal(18,2), including schema metadata used for casts.
Read only the paths listed in the manifest, not a recursive wildcard over a reused
output directory. Files not named by this run are not removed. Manifest entries
carry table, firm, row count, and SHA-256; metadata also records seed, snapshot,
effective configuration, and PyArrow version. Output roots are never embedded.

All ontology-required source attributes are emitted at their producers, not filled
with constants in Gold. Relationship partners, group heads, reviewers and approvers
reference real same-firm timekeepers; reviewers/approvers are the matter's billing
partner. Each practice's head is a partner in that practice (the first five
timekeepers lead the five practices). Entry `rate_cents` is the exact selected negotiated rate. Staff office and
department match the linked office/practice. All generated time is billable and all
generated costs are recoverable, consistent with the accounting model. Time and
budgets use the real whole-matter phase `all`, without duplicating phase actuals.
Guideline `rule_type=payment_terms` has a Decimal threshold in days (30.00), with
inclusive effective dates covering the generated invoices; optional fee caps remain
separate cents attributes. Metadata also specifies major-currency units for fee-cap
rules. Source access effects remain `grant`/`deny`; the ontology expression maps
them to `granted`/`screened` respectively, not the reverse.

## Accounting definitions

- Money is **integer cents**, not a binary float. Hours use `Decimal`. Multiplication
  and percentage rounding use half-up rounding to a cent. Display ratios are
  six-place decimal strings, not monetary inputs.
- `standard_value_cents = hours × timekeeper.standard_rate_cents`. The timekeeper's
  `standard_rate_currency` must equal the matter, negotiated rate, and entry currency.
  USD and GBP timekeepers are distinct. There is no exchange-rate conversion.
- `value_cents = hours × negotiated hourly_rate_cents`. This is recorded negotiated
  time before reductions and is the fee-budget actual basis. Costs are excluded.
- Proforma `gross_time_cents` sums original entry values; `gross_cents` subtracts
  time-entry reductions and adds disbursements. `discount_cents` is **only** the
  proforma-stage reduction. `net_cents = gross_cents - discount_cents`.
- Issued invoices preserve the converted proforma amounts exactly.
  `net_time_cents = net_cents - disbursement_cents`. Both document tables also carry
  reconciled decimal `hours`, `time_entry_count`, and `disbursement_count`.
- Adjustment stages are `time_entry`, `proforma`, or `invoice`. Exactly one matching
  FK is non-null. An invoice-stage adjustment must not also carry a proforma ID.
  Resolve upstream targets through their actual document relationships for cohort
  attribution. Never sum one adjustment again after joining multiple time entries.
- All adjustments are positive fee reductions, never negative refunds. Cash is
  strictly positive and never exceeds issued net minus invoice write-offs.
- Issued pre-bill realization is **issued net fees / standard time for the same
  issued-invoice cohort and currency**, excluding costs. Invoice write-offs reduce
  recoverable fees/AR but **do not change already issued net realization**.
  Claiming that both write-downs and write-offs lower issued billed/standard is false.
- The snapshot has three mutually exclusive activity states: `unbilled` (no document),
  `proforma` (unconverted draft), and `billed` (issued invoice). Draft WIP and unbilled
  time are reported separately. The fixed-fee-capped matter still records time WIP;
  its negotiated time value is not a claim of collectible fixed-fee revenue.
- No taxes, refunds, FX, retainers, cross-currency settlements, reversal journal,
  fee-cap enforcement, or parent consolidation is modeled. Terms and fee caps are
  descriptive guidelines; synthetic invoices are not an accounting system of record.

## Default planted metrics

Amounts here describe the default inputs; the generated expected-answer JSON is
the authoritative calculated result, not a hard-coded answer script.

| Metric | Harbor | Kestrel |
|---|---:|---:|
| Clients / matters / timekeepers | 15 / 60 / 40 | 10 / 25 / 15 |
| Time entries | 8,000 | 2,500 |
| Meridian / Asterwick unbilled entries / hours | 200 / 800 | 80 / 320 |
| Meridian / Asterwick negotiated unbilled fees, USD | $432,000 | $172,800 |
| Meridian / Asterwick standard unbilled fees, USD | $480,000 | $192,000 |
| Meridian / Asterwick WIP age range | 60–120 days | 60–120 days |
| Castellan / Dalesmere employment fees vs budget, USD | $54,000 / $40,000 | $54,000 / $40,000 |
| Employment overrun | $14,000 / 35% | $14,000 / 35% |
| Castellan / Dalesmere leases fees vs budget, USD | $43,200 / $40,000 | $43,200 / $40,000 |
| Lease overrun | $3,200 / 8% | $3,200 / 8% |
| Solace / Highmere 90-day net credit sales, USD | $47,151.72 | $47,151.72 |
| Solace / Highmere collectible AR, USD | $49,771.26 | $49,771.26 |
| Solace / Highmere rolling-90-day sales DSO | 95 days | 95 days |
| Solace / Highmere actual invoice-to-cash application mean | 95 days | 55 days |

Employment hours are 135 against 100; lease hours are 108 against 100. Both amounts
and hours use the same current-quarter dates. **Two distinct DSO methods** are
available; neither is merely the oldest invoice age:

- `solace_dso` retains collectible AR / rolling-90-day net credit sales × 90. Its
  unpaid invoice ages remain 95 and 30 days. Two additional fully paid invoices,
  aged 150 and 120 days, are outside the sales window and contribute zero AR, so
  the documented sales-ratio amounts above remain unchanged.
- `solace_cash_application_dso` computes actual `paid_on - issued_on` per cash
  application through the snapshot. Solace's older invoices are fully paid at 90
  and 100 days, averaging **95 days**; Highmere's are paid at 50 and 60 days,
  averaging **55 days**. All payment dates precede the snapshot. Each has one full
  application and equal value, so the cash-weighted and completed-invoice means
  also agree. Ordinary extension matters exclude these two clients, so their full
  client-level cash-application means also match, not just the seeded matter means.
  The general helper includes partial applications if present, excludes
  unpaid invoices/future cash, and returns null for an empty observed cohort.

Costs and invoice write-offs are absent from these cases. The `solace_*` keys are
retained as compatibility scenario labels for both firms; Kestrel's client is
Highmere, not Solace. Cash lags are firm-configurable, while the total time-entry
counts remain exact. Kestrel also retains its different WIP volume and opposite
litigation leakage mix rather than copying Harbor's full financial distribution.

Vantage / Foxglade (`m008`) is proforma-write-down dominated by at least 3:1 over its invoice
write-offs; Redgrave / Greystone (`m009`) is invoice-write-off dominated by at least 3:1 over
its draft write-downs, in **both** firms. Harbor's authorized last-month litigation
cohort is proforma-dominated, while Kestrel's is invoice-dominated, each at least
3:1 under defaults. Issued pre-bill realization declines from July to August in
both firms due to pre-issue concessions, not invoice write-offs. Generic litigation
activity remains in the matched denominators; exact Q3 totals are calculated from
the emitted rows. Adjustments are attributed to the **issue month of their linked
invoice**, using all reductions recognized through the snapshot. This is not a
reduction-recognition-month analysis, and draft-only reductions are excluded.

Northwind / Cairnwell is the healthy IP case; issued balances are fully paid when old enough,
with no invoice write-offs. Dunmore / Emberfell (`m007`) uses GBP and the UK entity. All other
default matters, including every other planted case, are USD. Do not sum GBP with USD.

## Local factual answers and security boundary

[generators/expected.py](generators/expected.py) provides `aged_wip` (Q1),
`quarter_variances` (Q2), `realization_cohort` (Q3), `dso_metrics`, `cash_application_metrics`, and
`compute_expected_answers`. Q4 calls `secure_billing_position` and returns factual
amounts and source IDs, not a simulated agent-generated paragraph.

[generators/security.py](generators/security.py) provides `accessible_matter_ids`,
`authorized_rows`, `require_matter`, `sum_by_currency`, `secure_sum`, and
`secure_billing_position`. Deny overrides grant; missing identity or access denies;
foreign-tenant identity denies; parent grants do not inherit; unknown policy effects
deny. Filtering happens **before** aggregation. `authorized_rows` refuses non-matter
tables, which must first be joined through an authorized matter by the caller.

Ashworth / Bellhaven (`m003`) deliberately has both a grant and a deny for the first partner,
plus a reserved synthetic canary in matter and time narratives. Restricted rows
are planted in both current-quarter activity and prior-month litigation to expose
aggregate leaks. Golden outputs contain no screened name, ID, financial information,
or canary; Q5 is a generic denial. Tests inflate restricted financials and verify that
all partner expected answers remain byte-identical.

**These helpers are a local test oracle, not production access control.** The raw
Parquet, broad explicit grants, manifest configuration, and schema are provider-side
synthetic artifacts. Do not share raw source output or the multi-firm golden file
with a customer/partner. Deploy tenant isolation and policy enforcement separately;
an access table, refusal string, or canary test does not prove a secure data plane.

## Tests and reproducibility

[tests](tests) covers complete FK/tenant integrity, all-table ID uniqueness, exact
volumes, parent cycles, distinct partner roles, date validity, decimal money,
document conservation, payment ceilings/no refunds, exclusive adjustment provenance,
aged WIP, quarter overruns, both interpretations of two-firm leakage differentiation,
matched denominators, write-off invariance, DSO, canary and aggregate non-interference,
deterministic bytes, schema round trips, CLI dry-run, config validation, and leap-day /
quarter-boundary snapshots. `validate_dataset` also enforces invariants in normal
generation before serialization.

Byte-identical output is promised for identical effective inputs and the same
Python/PyArrow/runtime environment. Cross-version writer bytes are not promised;
PyArrow's writer version is recorded. Output uses sorted rows/files, fixed schemas,
fixed Parquet options, UTF-8 JSON with LF line endings, and no run timestamp.

The caller is responsible for environment setup and test execution. No Python or
test commands were run as part of this implementation.