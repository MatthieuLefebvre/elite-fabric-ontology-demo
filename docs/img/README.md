# Diagram sources and rendered companions

[architecture.mmd](architecture.mmd) and [leakage.mmd](leakage.mmd) are the editable
Mermaid sources. [architecture.svg](architecture.svg) and [leakage.svg](leakage.svg)
were rendered from them with **@mermaid-js/mermaid-cli 11.4.2**, using temporary npm
package execution and a white background; no global installation is required.
They are actual rendered companions, not screenshots or independently authored art.

The rendering command is `npx --yes --package @mermaid-js/mermaid-cli@11.4.2 mmdc`
with `--input docs/img/architecture.mmd --output docs/img/architecture.svg
--backgroundColor white`; repeat with `leakage` as both basenames, from the repository
root. The renderer downloads its npm/browser dependencies on first use. That pinned
release emits a deprecated-Puppeteer warning; use it for these trusted local diagram
sources only, and review a newer renderer before processing untrusted input.

Both sources include `accTitle` and `accDescr`, retained as SVG title/description.
Plain SVG text avoids dependence on embedded HTML labels. Keep source and output
together when updating a diagram. The architecture uses dashed arrows and explicit
STOP labels for manual or blocked stages; it is not evidence of a cloud deployment.