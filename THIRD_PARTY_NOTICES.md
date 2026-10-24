# Third-party notices and build provenance

The project license is in `LICENSE`. Dependencies retain their own copyrights and license terms. This file records the components inspected for the Windows Python 3.11 build; it does not replace their license texts. Preserve the `licenses` directory when redistributing a packaged build.

## Python application dependencies

The following texts were copied without modification from the installed distributions used for the build. Their paths below are relative to `licenses/`.

| Component | Inspected version | Upstream | License text location |
| --- | --- | --- | --- |
| CPython | 3.11.9 | https://www.python.org/ | `Python-3.11.9-LICENSE.txt` |
| NumPy and its wheel's bundled numerical libraries | 1.26.4 | https://numpy.org/ | `numpy/numpy-1.26.4.dist-info/LICENSE.txt`, plus the additional NumPy notices beneath `numpy/` |
| sounddevice | 0.5.6 | https://python-sounddevice.readthedocs.io/ | `sounddevice/sounddevice-0.5.6.dist-info/licenses/LICENSE` |
| CFFI | 2.0.0 | https://cffi.readthedocs.io/ | `cffi/cffi-2.0.0.dist-info/licenses/` |
| pycparser | 2.22 | https://github.com/eliben/pycparser | `pycparser/pycparser-2.22.dist-info/LICENSE` |
| cryptography | 46.0.3 | https://cryptography.io/ | `cryptography/cryptography-46.0.3.dist-info/licenses/` |
| bcrypt, if present in the package | 5.0.0 | https://github.com/pyca/bcrypt | `bcrypt/bcrypt-5.0.0.dist-info/LICENSE` |
| psutil, if present in the package | 7.0.0 | https://github.com/giampaolo/psutil | `psutil/psutil-7.0.0.dist-info/LICENSE` |
| PyYAML, if present in the package | 6.0.2 | https://pyyaml.org/ | `PyYAML/PyYAML-6.0.2.dist-info/LICENSE` |
| PyInstaller bootloader/build tooling | 6.14.2 | https://pyinstaller.org/ | `pyinstaller/pyinstaller-6.14.2.dist-info/COPYING.txt` (includes the bootloader exception) |
| Tk | 8.6 | https://www.tcl.tk/ | `Tk-8.6-license.terms` (copied from the packaged Tk runtime) |

The complete CPython license includes additional acknowledgements for components in its runtime. The NumPy distribution license includes notices and terms for libraries shipped in that wheel. Build tooling need not be included as runnable software in the application distribution.

## Audio and cryptographic libraries

The non-ASIO PortAudio Windows library supplied with sounddevice comes from [spatialaudio/portaudio-binaries](https://github.com/spatialaudio/portaudio-binaries), whose installed provenance identifies PortAudio by Ross Bencina and Phil Burk. `PortAudio-LICENSE.txt` is copied from [PortAudio v19.7.0](https://github.com/PortAudio/portaudio/blob/v19.7.0/LICENSE.txt). This attribution does not establish a particular binary's exact source revision. ASIO-enabled variants also include Steinberg components; their separate provenance and terms need review before distributing them. An x64 MME/WASAPI application needs the non-ASIO `libportaudio64bit.dll`.

The inspected CPython runtime reports OpenSSL 3.0.13; the inspected cryptography wheel reports OpenSSL 3.5.4. Their license texts are copied from the matching official OpenSSL tags into `OpenSSL-3.0.13-LICENSE.txt` and `OpenSSL-3.5.4-LICENSE.txt`:

- https://github.com/openssl/openssl/blob/openssl-3.0.13/LICENSE.txt
- https://github.com/openssl/openssl/blob/openssl-3.5.4/LICENSE.txt

## External Rocksmith tools and optional native extension

Rocksmith, song archives, profiles, Steam account data, and Rocksmith Toolkit runtime DLLs are not project source assets. The metadata and split helpers are built against an independently installed Rocksmith Toolkit and load that installation's libraries at runtime. A user must obtain the Toolkit separately; these notices do not grant redistribution rights to its binaries or to game/song assets.

The optional native extension is derived from [Lovrom8/RSMods](https://github.com/Lovrom8/RSMods) at commit `1e4a67d43dfd0bae2b613edc4cd67b922332dc61`. On 2026-10-24, the pinned Git tree contained no project-wide LICENSE, COPYING, or NOTICE file, and official GitHub repository metadata reported no identified license. Vendored libraries have individual notices, but those do not establish a license for the RSMods project as a whole. The existence of upstream release downloads is recorded as provenance, not as an affirmative redistribution permission.

The JonsRockSmithMods `LICENSE` must not be represented as granting rights to all upstream RSMods code. A public distribution must establish permission for the modified native binary separately, or omit that optional binary. Local development/build artifacts do not belong in public source archives merely because they exist on the build machine.

## Distribution boundaries

Do not include local logs, preferences, profiles, song archives, account identifiers, debugging symbols, build directories, or backup copies. Public source archives and Windows packages should be assembled from explicit file allowlists and checked independently before upload. Dependency updates require refreshing their version information and notices.
