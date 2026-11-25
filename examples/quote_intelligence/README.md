# Quote Intelligence demo fixture

This directory contains synthetic, fictional supplier quotations for exercising Muse Quote Intelligence. It does not contain real supplier offers or purchasing recommendations.

## Generate the sample report

From the Muse project root on Windows:

```powershell
py -3.11 -m muse.quote_intelligence examples/quote_intelligence/demo_quotes.json --format markdown --output outputs/quote_intelligence-demo.md
```

The sample intentionally includes two comparable SGD quotes and one USD quote with unknown tax status. The engine must keep currencies separate, rank only the comparable SGD group, preserve source locators, and require human review. No supplier is automatically selected.

## Current limitations

- Input is normalized JSON only; PDF/XLSX/OCR extraction is not implemented.
- Source references are synthetic examples, not verified documents.
- No vendor verification, external upload, purchase order, approval, or payment is performed.
- This is a local demo/prototype, not a validated commercial product.