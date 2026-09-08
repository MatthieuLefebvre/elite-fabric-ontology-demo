# ISV ontology package example

This folder demonstrates how an ISV can distribute the portable schema of the Elite
legal financial ontology. It is an illustrative package, not a captured customer
deployment and not evidence that every target Fabric environment imports every RDF
construct unchanged.

## Package contents

| File | Purpose |
| --- | --- |
| `ontology.ttl` | Canonical, reviewable Turtle artifact. |
| `ontology.rdf` | Generated RDF/XML compatibility artifact. |
| `manifest.json` | Package identity, version, formats, scope, and exclusions. |
| `checksums.sha256` | Integrity hashes for the two ontology artifacts. |
| `CHANGELOG.md` | Consumer-visible ontology package history. |
| `LICENSE` | License distributed with the package. |

The ontology contains 14 entity classes, 29 object properties, and six representative
datatype properties. The object-property topology mirrors `fabric/ontology/`, which
remains the authoring source for this repository.

## ISV release rules

1. Replace the reserved `https://isv.example/` namespace with a stable HTTPS namespace
   controlled by the ISV before a production release. Never derive ontology IRIs from a
   customer tenant, workspace, or lakehouse ID.
2. Update `ontology.ttl` from the authoring contract or from the approved Fabric export.
   Do not edit `ontology.rdf` independently.
3. Generate RDF/XML from Turtle, validate both files, and require graph equivalence.
4. Increment `packageVersion`, `owl:versionInfo`, and `owl:versionIRI` together. Record
   compatibility-impacting changes in `CHANGELOG.md`.
5. Recalculate `checksums.sha256` after generating the artifacts.
6. Test the release by importing it into a clean, nonproduction Fabric workspace.

With RDFLib installed, generation and validation can be performed with:

```powershell
python -c "from rdflib import Graph; g=Graph(); g.parse('ontology.ttl', format='turtle'); g.serialize('ontology.rdf', format='xml', encoding='utf-8')"
python -c "from rdflib import Graph; from rdflib.compare import isomorphic; a=Graph().parse('ontology.ttl', format='turtle'); b=Graph().parse('ontology.rdf', format='xml'); assert isomorphic(a, b); print(len(a))"
Get-FileHash ontology.ttl, ontology.rdf -Algorithm SHA256
```

## Deployment boundary

This package carries ontology semantics only. Deploy customer-specific source bindings,
Fabric item identifiers, permissions, and security policy through a separate environment
configuration. Importing the ontology does not grant data access and does not replace
authorization enforcement at the data boundary.

An actual Fabric export can include additional product metadata or supported vocabulary.
Preserve that metadata unless the ISV has verified that removing it survives an
export-import round trip in the target Fabric release.