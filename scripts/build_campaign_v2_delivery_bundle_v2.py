from __future__ import annotations
import hashlib
import json
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/campaign_videos_20261124_v2'
def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()
def main() -> None:
    out = BASE / 'campaign-v2-delivery-v2.zip'
    if out.exists():
        raise FileExistsError(out)
    source = json.loads((BASE / 'campaign-video-manifest.json').read_text(encoding='utf-8'))
    files = []
    docs = ['LAUNCH-EXPERIMENT-PLAN.md', 'PLATFORM-RELEASE-GATES.md', 'SOCIAL-PUBLISHING-DRAFTS.md', 'LANDING-PREVIEW-README.md', 'campaign-landing-preview.html', 'campaign-video-manifest.json']
    for name in docs:
        path = BASE / name
        if not path.is_file():
            raise FileNotFoundError(path)
        files.append((path, 'campaign/' + name))
    for episode in source['episodes']:
        video = ROOT / episode['video'].replace(chr(92), '/')
        package = ROOT / episode['package'].replace(chr(92), '/')
        if sha(video) != episode['sha256'] or sha(package) != episode['package_sha256']:
            raise RuntimeError('source episode hash mismatch: ' + episode['title'])
        key = video.parent.name
        files.append((video, 'campaign/media/' + key + '/' + video.name))
        files.append((package, 'campaign/packages/' + key + '-delivery-package.zip'))
    archive_names = [arc for _, arc in files]
    if len(archive_names) != len(set(archive_names)):
        raise RuntimeError('duplicate archive member path')
    readme = ('# Muse Campaign v2 delivery bundle\n\nStatus: INTERNAL REVIEW ONLY. This bundle is not public-release authorization.\n\n'
              'Includes three 55-second 1080x1920 silent explainer episodes, their individual delivery packages, the campaign manifest, '
              'five-platform draft copy, landing-page preview, and a seven-day low-cost validation plan.\n\n'
              'Release gates remain closed: rights/editorial review pending, no social account authorization, no public deployment, '
              'no payment provider, no orders, and US$0 settled revenue. Price shown is a US$9 hypothesis only, not a validated or live offer.\n\n'
              'Review all 15 scenes, source claims, mobile safe areas, font/model/asset licenses, and AI disclosure for each destination before any public release.\n')
    temp = BASE / 'campaign-v2-package-readme.tmp'
    temp.write_text(readme, encoding='utf-8')
    hashes = {arc: sha(path) for path, arc in files}
    hashes['campaign/README.md'] = sha(temp)
    manifest = {'schema': 'muse.campaign-delivery-manifest.v1', 'status': 'internal_review_only',
                'source_manifest_status': source['status'], 'files': hashes, 'episodes': len(source['episodes']),
                'public_posting_authorized': False, 'public_deployment_authorized': False,
                'payments_enabled': False, 'sales': 0, 'settled_revenue_usd': '0.00'}
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(temp, 'campaign/README.md')
        archive.writestr('campaign/delivery-manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        for path, arc in files:
            archive.write(path, arc)
    temp.unlink()
    with zipfile.ZipFile(out) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError('bundle CRC failed: ' + bad)
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise RuntimeError('duplicate ZIP members')
        for arc, digest in hashes.items():
            if hashlib.sha256(archive.read(arc)).hexdigest() != digest:
                raise RuntimeError('bundle member SHA mismatch: ' + arc)
        if set(archive.namelist()) != set(hashes) | {'campaign/delivery-manifest.json'}:
            raise RuntimeError('bundle member set mismatch')
        member_count = len(archive.namelist())
    print(json.dumps({'status': 'passed', 'path': str(out.relative_to(ROOT)), 'sha256': sha(out),
                      'member_count': member_count, 'episode_count': len(source['episodes']), 'zip_crc': 'passed',
                      'duplicate_member_paths': False, 'release_state': 'internal_review_only',
                      'public_posting_authorized': False, 'public_deployment_authorized': False,
                      'payments_enabled': False}, ensure_ascii=False, indent=2))
if __name__ == '__main__':
    main()
