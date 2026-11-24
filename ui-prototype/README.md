# Muse Prometheus landing page preview

This is a local-only preview of the Muse commercial showcase. It is not deployed to a public domain.

## Start the local showcase and inquiry inbox

From the Muse project root on Windows:

    py -3.11 scripts/muse-lead-server.py --host 127.0.0.1 --port 8765

Open http://127.0.0.1:8765/showcase.html on the same computer. The server binds to loopback only and accepts same-origin JSON submissions at /api/leads. The inquiry form requires explicit consent and a reply contact, applies a honeypot and per-IP in-memory rate limit, and returns a reference only after the local SQLite transaction commits.

The default database is data/commerce/leads.sqlite3. It stores submitted contact details and project briefs in plaintext on this computer, with a 90-day retention window. Protect the Windows user account and the project directory. Do not put passwords, payment data, identity documents, health details or other sensitive information in the form. No lead data is sent to a third party, and request logs omit IPs and form contents.

## Manage local inquiries

Run `py -3.11 scripts/muse-leads.py list` to view recent references without contact details. Use `show MUSE-YYYYMMDD-XXXXXXXX` only when you need the full brief/contact. `export --output outputs/commerce-leads-export.csv` writes a new CSV containing contact details; it refuses to overwrite. Exports are sensitive and must be stored/accessed accordingly.

## Validation

    py -3.11 scripts/muse_dev.py run --language python --script "import unittest; suite=unittest.defaultTestLoader.discover('tests', pattern='test_lead_intake.py'); result=unittest.TextTestRunner(verbosity=2).run(suite); raise SystemExit(not result.wasSuccessful())"

## Public-deployment boundary

This is a local development inbox, not a public production service. Do not expose it through port forwarding or bind it to a public/LAN interface. Before public launch, move intake to a managed HTTPS backend or an authorized CRM such as HubSpot; add authentication, encrypted storage, access controls, deletion/export workflows, backups, privacy/retention notices, monitoring and abuse protection. Verify the domain, service scope, pricing and customer-response process before taking orders.