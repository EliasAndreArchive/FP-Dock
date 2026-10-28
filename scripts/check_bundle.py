"""Smoke-test the actual packaged executable using disposable, synthetic data."""
from pathlib import Path
import json
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
bundle = json.loads((ROOT/'build/bundle.json').read_text())
executable = bundle['executable']
check = Path(tempfile.mkdtemp(prefix='bundle-check-', dir=ROOT/'build'))
env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
report = check/'window.png'
subprocess.run([executable, str(ROOT/'Demo/CINEMA'), '--state-dir', str(check/'state'),
                '--smoke-test', str(report), '--headless-check'], env=env, check=True, timeout=90)
assert report.exists(), 'Packaged application did not render a window'
subprocess.run([executable, '--self-test-transfer', str(check/'transfer')],
               env=env, check=True, timeout=90)
result = json.loads((check/'transfer/result.json').read_text())
assert result['frozen'] and result['source_unchanged'] and not result['unselected_video_copied'], result
print(json.dumps(result))
