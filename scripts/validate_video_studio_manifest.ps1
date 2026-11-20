$ErrorActionPreference = 'Stop'
$env:PYTHONPATH = 'src'

py -3.11 -c "import sys,unittest; sys.path.insert(0,'tests'); from test_video_studio import VideoStudioTests; suite=unittest.defaultTestLoader.loadTestsFromTestCase(VideoStudioTests); result=unittest.TextTestRunner(stream=sys.stdout,verbosity=2).run(suite); sys.exit(not result.wasSuccessful())"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

py -3.11 -m py_compile src/muse/video_studio.py tests/test_video_studio.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

py -3.11 -c "import tomllib; data=tomllib.load(open('pyproject.toml','rb')); assert set(data['tool']['setuptools']['package-data']['muse']) == {'video_pipelines.yaml','video_stages.yaml'}; print('package-data: valid')"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

py -3.11 -m muse.video_studio pipelines | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output 'VIDEO_STUDIO_MANIFEST_VALIDATION_OK'
