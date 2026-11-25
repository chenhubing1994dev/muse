# Muse Quote Intelligence

- Human review required: **yes**
- No automatic supplier selection.
- Missing prices and scope items are treated as unknown, never as zero.

## Comparison groups

### SGD · tax status: included

- Comparable: **yes**
- Reasons: none

| Rank | Supplier | Quote ID | Quoted total |
|---:|---|---|---:|
| 1 | Demo Supplier B (fictional) | demo-sgd-b | 3270.00 SGD |
| 2 | Demo Supplier A (fictional) | demo-sgd-a | 3380.00 SGD |

### USD · tax status: unknown

- Comparable: **no**
- Reasons: fewer_than_two_quotes, tax_status_unknown

## Quote details

### Demo Supplier A (fictional) (demo-sgd-a)

- Currency / tax: SGD / included
- Subtotal: 3300.00 SGD
- Adjustments: 80.00 SGD
- Quoted total: 3380.00
- Missing scope items: none identified
- Risk flags: scope_exclusions_listed

Price source references:
- equipment: DEMO-A.pdf · page 1, row 1 — Commercial air-conditioning unit SGD 2,800.00
- installation: DEMO-A.pdf · page 1, row 2 — Installation and commissioning SGD 500.00
- fee Site visit: DEMO-A.pdf · page 2, row 1 — Site visit fee SGD 80.00

### Demo Supplier B (fictional) (demo-sgd-b)

- Currency / tax: SGD / included
- Subtotal: 3370.00 SGD
- Adjustments: -100.00 SGD
- Quoted total: 3270.00
- Missing scope items: none identified
- Risk flags: scope_exclusions_listed

Price source references:
- equipment: DEMO-B.pdf · page 1, row 1 — Commercial air-conditioning unit SGD 2,950.00
- installation: DEMO-B.pdf · page 1, row 2 — Installation and commissioning SGD 420.00
- discount Project discount: DEMO-B.pdf · page 2, row 1 — Project discount SGD 100.00

### Demo Supplier C (fictional) (demo-usd-c)

- Currency / tax: USD / unknown
- Subtotal: 2450.00 USD
- Adjustments: 0.00 USD
- Quoted total: 2450.00
- Missing scope items: none identified
- Risk flags: tax_status_unknown, warranty_not_stated, payment_terms_not_stated

Price source references:
- equipment: DEMO-C.pdf · page 1, row 1 — Commercial air-conditioning unit USD 2,100.00
- installation: DEMO-C.pdf · page 1, row 2 — Installation and commissioning USD 350.00

## Decision boundary

This report is a comparison aid, not procurement approval. Confirm original documents, scope, tax treatment, exclusions, supplier credentials, and contract terms before making a decision.

