import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import warnings
import zipfile

from tools.build_windows_offline_bundle import allowed
from tools.verify_windows_offline_bundle import verify, REQUIRED_FILES
from oneil_runtime_v2.preflight import inspect


class BundleTests(unittest.TestCase):
    def manifest(self, files):
        return {'format': 'oneil.offline.source.v1', 'source_commit': 'a'*40,
                'mode': 'OFFLINE_SIMULATION_SOURCE_ONLY', 'live_capable': False,
                'installed': False, 'deployed': False, 'dependencies_bundled': False,
                'files': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in files.items()}}

    def archive(self, path, files, manifest=None, complete=True):
        if complete:
            baseline = {name: b'# public synthetic fixture' for name in REQUIRED_FILES}
            files = {**baseline, **files}
            if manifest is not None:
                manifest = {**manifest, 'files': {**self.manifest(baseline)['files'], **manifest['files']}}
        with zipfile.ZipFile(path, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
            z.writestr('MANIFEST.json', json.dumps(manifest or self.manifest(files)))

    def test_known_files_only_for_export(self):
        for name in ('oneil_runtime_v2/health.py', 'oneil_execution_v2/schema.sql',
                     'deploy/windows/RUNBOOK.md', 'tools/build_windows_offline_bundle.py',
                     'oneil_policy_v2/engine.py', 'tests/policy_v2/test_engine.py'):
            self.assertTrue(allowed(name))
        for name in ('.env', 'live_trade.py', 'oneil_runtime_v2/secret.sqlite',
                     'oneil_execution_v2/account.json', '.git/config', 'v5_7_report.json'):
            self.assertFalse(allowed(name))

    def test_integrity_pass_is_not_authenticity_or_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'bundle.zip'
            self.archive(path, {'oneil_runtime_v2/example.py': b'# sample\n'})
            result = verify(path)
            self.assertTrue(result['verified'])
            self.assertFalse(result['live_capable'])
            self.assertFalse(result['publisher_authenticity_verified'])

    def test_mismatched_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'bundle.zip'
            self.archive(path, {'oneil_runtime_v2/example.py': b'changed'}, self.manifest({'oneil_runtime_v2/example.py': b'original'}))
            with self.assertRaisesRegex(ValueError, 'CONTENT_HASH_MISMATCH'): verify(path)

    def test_extra_missing_and_duplicate_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'bundle.zip'
            self.archive(path, {'oneil_runtime_v2/one.py': b'1'}, self.manifest({'oneil_runtime_v2/two.py': b'2'}))
            with self.assertRaises(ValueError): verify(path)
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with zipfile.ZipFile(path, 'a') as z: z.writestr('oneil_runtime_v2/one.py', b'1')
            with self.assertRaises(ValueError): verify(path)

    def test_unsafe_paths(self):
        for name in ('../escape.py', '/absolute.py', 'C:/path.py', 'a\\b.py', 'a/./b.py'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'bundle.zip'
                self.archive(path, {name: b'bad'})
                with self.assertRaises(ValueError): verify(path)

    def test_windows_alias_devices_and_prefix_conflicts(self):
        cases = (
            {'oneil_runtime_v2/health.py': b'a', 'oneil_runtime_v2/HEALTH.py': b'b'},
            {'oneil_runtime_v2/health.py.': b'a'}, {'oneil_runtime_v2/NUL.py': b'a'},
            {'oneil_runtime_v2/aux.txt': b'a'}, {'oneil_runtime_v2/COM1.py': b'a'},
            {'oneil_runtime_v2/name.py ': b'a'}, {'oneil_runtime_v2/a?.py': b'a'},
            {'oneil_runtime_v2/a.py': b'a', 'oneil_runtime_v2/a.py/b.py': b'b'},
        )
        for files in cases:
            with self.subTest(files=files), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'bundle.zip'
                self.archive(path, files)
                with self.assertRaises(ValueError): verify(path)

    def test_empty_payload_and_unreviewed_paths_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'bundle.zip'
            self.archive(path, {}, self.manifest({}), complete=False)
            with self.assertRaises(ValueError): verify(path)
            self.archive(path, {'live_trade.py': b'not permitted'})
            with self.assertRaises(ValueError): verify(path)

    def test_partial_combined_payload_is_rejected(self):
        # A matching manifest alone does not make an incomplete release valid.
        for missing in ('oneil_policy_v2/admission.py', 'oneil_runtime_v2/journal.py',
                        'tests/policy_v2/test_policy_admission.py',
                        'tools/validate_execution_v2_sql.py',
                        'tools/run_monitor_v2_tests.py'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'bundle.zip'
                files = {name: b'# synthetic fixture' for name in REQUIRED_FILES if name != missing}
                self.archive(path, files, complete=False)
                with self.assertRaisesRegex(ValueError, 'REQUIRED_SOURCE_MISSING'):
                    verify(path)

    def test_all_required_files_are_exportable(self):
        self.assertTrue(all(allowed(name) for name in REQUIRED_FILES))

    def test_preflight_rejects_missing_policy_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in REQUIRED_FILES - {'oneil_policy_v2/admission.py'}:
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('# synthetic fixture')
            result = inspect(root)
            self.assertIn('SOURCE_FILES_MISSING', result['reasons'])
            self.assertFalse(result['ready_to_trade'])

    def test_missing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'bundle.zip'
            with zipfile.ZipFile(path, 'w') as z: z.writestr('sample.py', b'1')
            with self.assertRaises(ValueError): verify(path)

    def test_false_live_label_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'bundle.zip'
            manifest = self.manifest({'oneil_runtime_v2/sample.py': b'1'})
            manifest['live_capable'] = True
            self.archive(path, {'oneil_runtime_v2/sample.py': b'1'}, manifest)
            with self.assertRaises(ValueError): verify(path)

    def test_preflight_is_not_readiness(self):
        result = inspect(Path(__file__).resolve().parents[2])
        self.assertFalse(result['live_capable'])
        self.assertFalse(result['ready_to_trade'])
        self.assertIn('BROKER_CONNECTIVITY', result['unverified'])


if __name__ == '__main__': unittest.main()
