# Muse Static Site Preview

- Pages: home/showcase, products, portfolio sample.
- The inquiry form submits consented requests to the local POST /api/leads endpoint.
- Start the local loopback-only lead server from the Muse project root: `py -3.11 scripts/muse-lead-server.py --static-root outputs/site_launch_preview_20261125_v2 --port 8765`.
- Leads are stored in the configured local SQLite database (default: data/commerce/leads.sqlite3) with a 90-day retention period.
- No analytics, payment processing, public deployment or third-party transmission is enabled.
- robots.txt disallows crawling; all pages remain a preview and must not be treated as a public launch.
- Font: Noto Sans CJK HK under SIL OFL 1.1; the rendered MP4 is included. Font file/license are bundled in the video delivery package, not duplicated in this site ZIP.
- Before deployment: complete editorial/source and commercial-rights review, review legal/privacy copy, configure an owner-controlled contact channel, confirm domain/HTTPS, access controls and monitoring, and deliberately remove the crawl block only after approval.
- Payments are not enabled; no price is represented as a live offer.
