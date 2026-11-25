# Muse independent video Reviewer signer

## Security boundary

`muse.video_reviewer_signer` is a separate one-shot signing process. Run it under a dedicated OS service account that is different from the video Worker account and Runtime account. The private Ed25519 key is loaded only by this process. Worker and Runtime receive only the explicitly pinned public-key trust store.

The signer refuses to start unless:
- the private-key path is absolute, regular, non-symlink, and matches `MUSE_VIDEO_REVIEWER_PRIVATE_KEY_SHA256`;
- the key is Ed25519 and its public-key fingerprint matches `MUSE_VIDEO_REVIEWER_PUBLIC_KEY_SHA256`;
- the current process identity matches the configured Reviewer service SID/UID and differs from the Worker SID/UID;
- the private-key ACL/mode excludes the Worker identity and inherited/broad access.

On Windows, configure these variables in the Reviewer service account environment:

- `MUSE_VIDEO_REVIEWER_PRIVATE_KEY_FILE`
- `MUSE_VIDEO_REVIEWER_PRIVATE_KEY_SHA256`
- `MUSE_VIDEO_REVIEWER_PUBLIC_KEY_SHA256`
- `MUSE_VIDEO_REVIEWER_KEY_ID`
- `MUSE_VIDEO_REVIEWER_ID`
- `MUSE_VIDEO_REVIEWER_SIGNER_SERVICE_SID`
- `MUSE_VIDEO_WORKER_SERVICE_SID`
- `MUSE_VIDEO_REVIEWER_TRUST_STORE_OUTPUT`

The private-key file DACL must be explicit (no inherited ACEs) and limited to the Reviewer service SID, SYSTEM, and Administrators. The Worker SID must not be one of those principals. Do not run the signer as SYSTEM or an Administrator identity.

On POSIX, configure `MUSE_VIDEO_REVIEWER_SIGNER_SERVICE_UID` and `MUSE_VIDEO_WORKER_SERVICE_UID`; both must be distinct, non-root service UIDs. The private key must be owned by the Reviewer UID and have no group/other permissions.

## Register the production public key

Run the registration command as the dedicated Reviewer account after the private key and identity settings are provisioned:

```powershell
py -3.11 -m muse.video_reviewer_signer --workspace "D:\AI AGENT\Muse" register-public-key
```

The command writes a new trust-store file (it never overwrites an existing file) and prints:
- trust-store absolute path;
- trust-store SHA-256 digest;
- key ID and Reviewer ID;
- public-key fingerprint.

Set the following variables in the Runtime and Worker environment using the exact printed values:

- `MUSE_VIDEO_REVIEWER_TRUST_STORE`
- `MUSE_VIDEO_REVIEWER_TRUST_STORE_SHA256`

Runtime loads no trusted Reviewer keys when these settings are absent. A partial, malformed, modified, or fingerprint-mismatched trust store fails closed.

## Sign one reviewed execution Ticket

The Worker writes one `video_pipeline.artifacts_verified` event and waits for an independent `video_pipeline.review_approved` event. The human Reviewer must first complete the visual review and persist the approval in the append-only `VisualReviewLedger`.

Then invoke the signer as the dedicated Reviewer account:

```powershell
py -3.11 -m muse.video_reviewer_signer --workspace "D:\AI AGENT\Muse" sign --execution-task-id "<exact-running-execution-ticket-id>"
```

The signer rechecks the live Ticket lease, latest human approval, persisted Commander DAG and workflow binding, artifact manifest, MP4 and product ZIP digests, visual-review evidence, and latest ledger record. It appends exactly one signed review event. It refuses duplicate signing, stale or rejected reviews, changed approvals, mismatched artifacts, and non-model/safe-fallback plans.

The Worker default wait is 300 seconds. Configure `MUSE_VIDEO_REVIEWER_WAIT_SECONDS` to an integer from 0 through 1800 if needed. No reviewer event means no Attempt completion. The Runtime verifies the Ed25519 signature and consumes the nonce before it calls the Worker finalizer; only then is the InitiativeStore Attempt committed.

## Operational restrictions

- Never place the private key, its passphrase, or service credentials in the repository, TaskQueue payloads, Worker process environment, or test fixtures.
- Test-only private keys and injected public-key maps are not production trust roots.
- The signer is currently a one-shot command; an OS scheduler/service supervisor must invoke it after a human review is persisted. Automatic background polling is not enabled by this module.
- Public deployment, payments, and social publishing remain disabled until the full end-to-end acceptance evidence is complete.
