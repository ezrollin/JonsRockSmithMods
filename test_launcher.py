"""Static launcher contracts: never launch apps or install dependencies during tests."""
from pathlib import Path
import re
import unittest

class LauncherTests(unittest.TestCase):
    def source(self,name):return Path(__file__).with_name(name).read_text()
    def test_launcher_offers_distinct_source_and_exe_paths(self):
        text=self.source('Launch JonsRockSmithMods.bat')
        self.assertIn('choice /C EP',text)
        self.assertIn('call "%~dp0Run EXE.bat"',text)
        self.assertIn('call "%~dp0Run Python.bat"',text)
    def test_python_launcher_never_runs_exe(self):
        text=self.source('Run Python.bat')
        self.assertIn('py -3.11 app.py',text)
        self.assertIn('.venv\\Scripts\\python.exe',text)
        self.assertNotIn('JonsRockSmithMods.exe',text)
        self.assertIn("struct.calcsize('P') == 8",text)
    def test_exe_launcher_prefers_flat_current_program(self):
        text=self.source('Run EXE.bat')
        self.assertLess(text.index('goto flat'),text.index('goto legacy'))
        self.assertIn('"%~dp0JonsRockSmithMods.exe"',text)
        self.assertNotIn('py -3.11',text)
    def test_setup_uses_isolated_venv_and_pinned_requirements(self):
        text=self.source('Setup Python.bat')
        self.assertIn('py -3.11 -m venv',text)
        self.assertIn('-m pip install -r "%~dp0requirements.txt"',text)
        self.assertNotIn('--user',text)
        self.assertIn("sys.version_info[:2] == (3,11)",text)
    def test_every_batch_goto_label_exists_and_errors_are_visible(self):
        for name in ('Launch JonsRockSmithMods.bat','Run Python.bat','Run EXE.bat','Setup Python.bat'):
            text=self.source(name);labels=set(re.findall(r'^:([a-z_]+)$',text,re.M))
            self.assertLessEqual(set(re.findall(r'\bgoto ([a-z_]+)',text)),labels,name)
            if name.startswith(('Run','Setup')):
                self.assertIn('echo ERROR:',text);self.assertIn('exit /b 1',text)

if __name__=='__main__':unittest.main()
