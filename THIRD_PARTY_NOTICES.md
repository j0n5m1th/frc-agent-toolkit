# Third-party notices

This project's own code is under the [GNU Affero General Public License v3](LICENSE) (AGPL-3.0-only). The software below is permissively licensed (MIT, BSD, Apache-2.0, ISC) or LGPL, which can be used with AGPL code. This project uses it. **None of it is copied into this repository or shipped inside it.** `uv` downloads each package from PyPI when the servers start, under that package's own license. Those licenses go with the packages, and you can read them in the installed package folders or on PyPI. Versions are pinned in `plugins/frc/uv.lock`.

This list names the packages the servers need directly. Their own dependencies (about 80 more) are in the lock file; the licenses we saw there are MIT, BSD, Apache-2.0, ISC, PSF, MPL-2.0 (`certifi`, `pathspec`) and Unlicense (`email-validator`), plus the ones named below.

| Package | What we use it for | License |
|---|---|---|
| `pyntcore`, `robotpy-wpiutil` and their `robotpy-native-*` parts | NetworkTables 4 client and WPILib utilities (RobotPy bindings of WPILib's [allwpilib](https://github.com/wpilibsuite/allwpilib)) | BSD-3-Clause |
| `fastmcp-slim` (it brings in `mcp`) | The MCP server framework | Apache-2.0 (FastMCP), MIT (mcp) |
| `pydantic` | Tool input and output models | MIT |
| `msgpack` | Decoding msgpack values from NetworkTables | Apache-2.0 |
| `protobuf` | Decoding protobuf values from NetworkTables | BSD-3-Clause |
| `paramiko` | SSH to the Systemcore | **LGPL-2.1** |
| `cryptography` | Making the SSH key the Systemcore tools use | Apache-2.0 or BSD-3-Clause |

## Notes

- **`paramiko` (LGPL-2.1).** It is installed as a separate package and imported as a library. This project does not copy or modify it, so you can replace it with another version of `paramiko` yourself. If you build something that bundles it with this project, the LGPL terms apply to your bundle.
- **`msvc_runtime` (Windows only).** On Windows, `uv` also installs this package from PyPI (it holds Microsoft's Visual C++ runtime and is marked proprietary). It is not part of this repository, and its own terms apply.
- **WPILib and NetworkTables.** This project follows WPILib's published [NetworkTables 4.1 protocol specification](https://github.com/wpilibsuite/allwpilib/blob/main/ntcore/doc/networktables4.adoc) and the WPILOG data log format. It contains no WPILib source code. WPILib is Copyright (c) FIRST and other WPILib contributors, under the BSD 3-Clause License.
- **CTRE.** The CTRE tool servers can download CTRE's free programs (such as Owlet, passerine and caniv) from `redist.ctr-electronics.com` only when you turn on `CTRE_ALLOW_INSTALL`. Those programs are CTRE's, under CTRE's own terms. Nothing from CTRE is shipped here, except `ctre-index.snapshot.json`: a copy of the file names, versions and URLs from CTRE's public tool index, kept as a fallback when the live index cannot be reached. The Phoenix Diagnostic Server reader is written from what was seen on a real device ([docs/CTRE.md](docs/CTRE.md)), not from CTRE source code.
- **Driver Station logs.** The `.dslog` reader follows the format as described by [AdvantageScope](https://github.com/Mechanical-Advantage/AdvantageScope) (which cites community notes). No AdvantageScope code is copied.
- **Earlier BSD-licensed contribution.** The log-phase work for 2027 (Utility mode, AdvantageKit entries, OpMode names) and its test file, `tests/wpilog/test_wpilog_timeline_2027.py`, were contributed by JosephTLockwood while this project was under the BSD 3-Clause License. That license permits the project's move to AGPL-3.0, but it requires this notice and text to stay with their contribution:

  > Copyright (c) 2026 JosephTLockwood. All rights reserved.
  >
  > Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:
  >
  > 1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
  > 2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
  > 3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.
  >
  > THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDER AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## Trademarks

FIRST(R) and FRC(R) are trademarks of FIRST. CTRE, Phoenix, Talon FX, CANcoder, Pigeon, CANivore, Limelight, WPILib, RobotPy, Claude, Anthropic and other product names are trademarks of their owners. They are used here only to say what the tools work with. This project is independent and is not affiliated with or endorsed by any of them.
