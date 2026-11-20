$ErrorActionPreference = 'Continue'
$env:PYTHONPATH = 'src'

py -3.11 -c "import sys,unittest; sys.path.insert(0,'tests'); from test_video_studio import VideoStudioTests; from test_video_workflow import VideoWorkflowTests; from test_video_prompt_composer import VideoPromptComposerTests; suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in (VideoStudioTests,VideoWorkflowTests,VideoPromptComposerTests)]); result=unittest.TextTestRunner(stream=sys.stdout,verbosity=2).run(suite); sys.exit(not result.wasSuccessful())" 2>&1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

py -3.11 -m py_compile src/muse/video_studio.py tests/test_video_studio.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

py -3.11 -c "import tomllib; tomllib.load(open('pyproject.toml', 'rb')); print('pyproject.toml: valid')"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

py -3.11 -m muse.video_studio pipelines | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output 'VIDEO_STUDIO_VALIDATION_OK'
