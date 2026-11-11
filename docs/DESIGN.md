# Refund Shark interface

Audience: a finance operator reviewing potential vendor service credits. Primary job: determine which claim is supported, inspect the evidence, and approve a draft.

Reference: Ramp's public accounts-payable product presentation, inspected October 29, 2026: https://ramp.com/accounts-payable. This is an independent demo inspired by its interface choices, not an official Ramp product or a copy of a proprietary component library.

Tokens: paper `#ffffff`, sidebar `#f7f7f4`, ink `#20221f`, secondary `#73766e`, border `#e7e8e2`, solar `#e5f253`. Semantic colors: forest `#326249` on `#edf5ef`, ochre `#886319` on `#fbf4e3`, muted slate for closed items. Use Inter Variable, bundled locally, for the interface; large financial totals use restrained weight and tabular figures. Native monospace is reserved for source hashes and audit identifiers.

Layout: fixed pale navigation rail, white canvas, small breadcrumb, wide page title, a single ruled financial summary, a compact action banner, then a filterable claims table. Claim details use a document-and-evidence split. No decorative hero, gradients, or oversized floating cards. The signature is an evidence rail connecting source → calculation → claim, with every calculation inspectable.

Controls: 6–8px corners; understated borders; lime only for the main action and shark mark. Tables carry real states, sorting, search, filters, selection, and CSV export. Tabs navigate meaningful views. Keyboard focus is dark and visible. On narrow screens, navigation collapses and tables scroll within their own container; the detail rail stacks. Honor reduced-motion preferences.

Critique: a generic revenue dashboard would hide the actual product. This layout gives most space to vendor, evidence, deadline, and decision. A successful state is not enough: refusal and missing evidence receive equal clarity. All money is labeled demo data; potential credits and received credits are separate.
