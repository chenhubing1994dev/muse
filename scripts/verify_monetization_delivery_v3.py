from __future__ import annotations
import hashlib, json, zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p: Path) -> str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()
def check_zip(path: Path, expected_names: set[str] | None=None) -> list[str]:
    with zipfile.ZipFile(path) as z:
        bad=z.testzip()
        if bad: raise RuntimeError(f'CRC failed: {path}: {bad}')
        names=set(z.namelist())
        if expected_names is not None and names != expected_names:
            raise RuntimeError(f'member set mismatch: {path}: {sorted(names)}')
        return sorted(names)
def main() -> None:
    base=ROOT/'outputs/creator_pilot_20261124'
    social=base/'social-content-kit-v3'
    social_json=json.loads((social/'social-content-kit.json').read_text(encoding='utf-8'))
    social_manifest=json.loads((social/'manifest.json').read_text(encoding='utf-8'))
    social_zip=social/'social-content-kit.zip'
    social_names=check_zip(social_zip, set(social_manifest['files'])|{'manifest.json'})
    for name,digest in social_manifest['files'].items():
        if sha(social/name)!=digest: raise RuntimeError(f'social manifest mismatch: {name}')
    with zipfile.ZipFile(social_zip) as z:
        for name,digest in social_manifest['files'].items():
            if hashlib.sha256(z.read(name)).hexdigest()!=digest: raise RuntimeError(f'social ZIP hash mismatch: {name}')
        if json.loads(z.read('manifest.json'))!=social_manifest: raise RuntimeError('embedded social manifest mismatch')
    if social_json.get('status')!='draft_only' or social_json.get('can_publish') is not False: raise RuntimeError('social release gate is not fail-closed')
    platforms={x['platform'] for x in social_json['platforms']}
    expected={'YouTube Shorts','TikTok','Instagram Reels','哔哩哔哩','小红书'}
    if platforms!=expected: raise RuntimeError(f'platform set mismatch: {platforms}')
    pilot=ROOT/'data/monetization/first-cantonese-pilot-2026-11-24-v7'
    pilot_manifest=json.loads((pilot/'manifest.json').read_text(encoding='utf-8'))
    vertical=pilot/'video-9x16.mp4'
    if sha(vertical)!=social_json['source_video']['sha256']: raise RuntimeError('social kit vertical video SHA mismatch')
    if sha(pilot/'video-16x9.mp4')!=social_json['horizontal_video']['sha256']: raise RuntimeError('social kit horizontal video SHA mismatch')
    check_zip(pilot/'delivery-package.zip')
    product_manifest=json.loads((base/'AI-Verification-Kit-v0.1.1.manifest.json').read_text(encoding='utf-8'))
    product_zip=base/'AI-Verification-Kit-v0.1.1.zip'
    if sha(product_zip)!=product_manifest['sha256']: raise RuntimeError('digital product ZIP SHA mismatch')
    product_names=check_zip(product_zip, set(product_manifest['entries']))
    if len(product_names)!=11 or product_manifest['entry_count']!=11: raise RuntimeError('digital product expected 11 entries')
    if product_manifest['price_hypothesis']!={'currency':'USD','amount':9.0,'validated':False}: raise RuntimeError('unexpected price hypothesis')
    if product_manifest['published'] is not False or product_manifest['orders']!=0 or product_manifest['settled_revenue']!=0 or product_manifest['commercial_use_allowed'] is not False: raise RuntimeError('product release/revenue state mismatch')
    html=(ROOT/'outputs/site_launch_preview_20261124_v3/products.html').read_text(encoding='utf-8')
    product_page_price_matches_manifest = 'US$9 测试假设' in html
    campaign=ROOT/'outputs/campaign_videos_20261124_v2'
    campaign_manifest=json.loads((campaign/'campaign-video-manifest.json').read_text(encoding='utf-8'))
    campaign_video_hashes=[]
    for episode in campaign_manifest['episodes']:
        video=ROOT/episode['video'].replace(chr(92), '/')
        package=ROOT/episode['package'].replace(chr(92), '/')
        if sha(video)!=episode['sha256']: raise RuntimeError(f"campaign video SHA mismatch: {episode['title']}")
        if video.stat().st_size!=episode['bytes'] or episode['full_decode']!='passed': raise RuntimeError(f"campaign video metadata mismatch: {episode['title']}")
        check_zip(package)
        if sha(package)!=episode['package_sha256']: raise RuntimeError(f"campaign package SHA mismatch: {episode['title']}")
        if episode['ready_to_publish'] is not False: raise RuntimeError('campaign release gate unexpectedly open')
        campaign_video_hashes.append(episode['sha256'])
    campaign_html=(campaign/'campaign-landing-preview.html').read_text(encoding='utf-8')
    campaign_plan=(campaign/'LAUNCH-EXPERIMENT-PLAN.md').read_text(encoding='utf-8')
    campaign_readme=(campaign/'LANDING-PREVIEW-README.md').read_text(encoding='utf-8')
    if 'US$9' not in campaign_html or 'US$9' not in campaign_plan or 'US$9' not in campaign_readme: raise RuntimeError('campaign price copy mismatch')
    if 'US$7' in campaign_html or 'US$7' in campaign_plan or 'US$7' in campaign_readme: raise RuntimeError('stale US$7 price remains')
    if campaign_manifest['public_posting_authorized'] is not False or campaign_manifest['public_deployment_authorized'] is not False or campaign_manifest['payments_enabled'] is not False: raise RuntimeError('campaign release gates unexpectedly open')
    result={'status':'passed','social_platforms':sorted(platforms),'social_zip_crc':'passed','social_zip_sha256':sha(social_zip),'vertical_video_sha256':sha(vertical),'horizontal_video_sha256':sha(pilot/'video-16x9.mp4'),'digital_product_entries':len(product_names),'digital_product_zip_crc':'passed','digital_product_zip_sha256':sha(product_zip),'website_price_copy':'US$9 hypothesis; unvalidated','campaign_videos':len(campaign_video_hashes),'campaign_video_hashes':campaign_video_hashes,'campaign_package_hashes_verified':True,'campaign_price_copy':'US$9 hypothesis; unvalidated','campaign_release_gates':'closed','product_page_price_matches_manifest':product_page_price_matches_manifest,'product_page_current_text':'US$7, conflicts with US$9 manifest' if not product_page_price_matches_manifest else 'US$9 matches manifest','published':False,'orders':0,'settled_revenue_usd':0,'external_deployment':False}
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
