"""Error types: one generic base in the shared package, one subclass per server, and no NT4 in shared code."""


from repo_layout import PACKAGE_DIRS

from frc_mcp_nt4.errors import NT4Error
from frc_mcp_wpilib.errors import WPILibMCPError
from frc_mcp_wpilog.errors import WPILogError

SHARED = PACKAGE_DIRS["frc_mcp_wpilib"]


def test_each_server_has_its_own_error_type_under_the_shared_base():
    assert issubclass(NT4Error, WPILibMCPError) and issubclass(WPILogError, WPILibMCPError)
    assert not issubclass(NT4Error, WPILogError) and not issubclass(WPILogError, NT4Error)


def test_shared_code_never_mentions_a_server_error_type():
    for path in SHARED.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "NT4Error" not in text and "WPILogError" not in text, path.name
