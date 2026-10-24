"""Opt-in real PSARC corruption checks: set JONS_HEALTH_TOOLKIT to a Toolkit folder."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from library_health import inspect_archive_health, inventory_audio_banks
from metadata.metadata import HELPER
from toolkit_runtime import resolve_toolkit_helper


@unittest.skipUnless(os.environ.get('JONS_HEALTH_TOOLKIT'), 'set JONS_HEALTH_TOOLKIT for real Toolkit fixture checks')
class NativeHealthTests(unittest.TestCase):
    def test_real_malformed_and_missing_chart_archives(self):
        toolkit = Path(os.environ['JONS_HEALTH_TOOLKIT']).resolve()
        helper = resolve_toolkit_helper(HELPER, toolkit)
        platform = 'x86' if 'x86' in helper.name else 'x64'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'Fixture.cs'
            source.write_text(r'''
using System;
using System.IO;
using System.Reflection;
using System.Text;
using RocksmithToolkitLib.PSARC;
class Fixture {
 static string toolkit;
 static void Main(string[] args) {
  toolkit=args[0];
  AppDomain.CurrentDomain.AssemblyResolve += (sender, e) => Assembly.LoadFrom(Path.Combine(toolkit, new AssemblyName(e.Name).Name+".dll"));
  Build(args[1],args[2]);
 }
 static void Build(string target,string manifest) {
  using(var archive=new PSARC(true)) {
   archive.AddEntry("manifests/songs_dlc/test_lead.json",new MemoryStream(Encoding.UTF8.GetBytes(File.ReadAllText(manifest))));
   archive.AddEntry("audio/windows/song_test.bnk",new MemoryStream(new byte[]{1,2,3}));
   using(var output=File.Create(target)) archive.Write(output,true,true);
  }
 }
}
''', encoding='utf-8')
            executable = root / 'Fixture.exe'
            result = subprocess.run([r'C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe', '/nologo', '/target:exe', f'/platform:{platform}', f'/out:{executable}', f'/reference:{toolkit / "RocksmithToolkitLib.dll"}', str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifest = root / 'manifest.json'
            archive = root / 'fixture.psarc'
            cases = [('{bad json', {'unreadable_manifest'}),
                     (json.dumps({'Entries': {'id': {'Attributes': {'ArrangementName': 'Lead', 'SongName': 'Synthetic', 'SongBank': 'song_test.bnk'}}}}), {'missing_chart', 'unreadable_audio_bank'})]
            for payload, expected in cases:
                manifest.write_text(payload, encoding='utf-8')
                result = subprocess.run([str(executable), str(toolkit), str(archive), str(manifest)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                inventory = inventory_audio_banks([archive], toolkit)
                self.assertTrue(inventory['complete'])
                self.assertIn('audio/windows/song_test.bnk', inventory['banks'])
                incomplete = inventory_audio_banks([archive, root / 'absent.psarc'], toolkit)
                self.assertFalse(incomplete['complete'])
                before = archive.read_bytes()
                report = inspect_archive_health(archive, toolkit)
                self.assertEqual(report['status'], 'issue_found')
                self.assertTrue(expected <= {i['code'] for i in report['issues']})
                self.assertEqual(archive.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
