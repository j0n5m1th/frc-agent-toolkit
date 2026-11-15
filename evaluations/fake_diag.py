"""A stand-in Phoenix Diagnostic Server (used by the tests and the functional test): a real HTTP server on localhost that answers the way the real one was seen to (Systemcore image alpha14, diagnostic server 2026.70.0).

The shapes, field names, error codes and the habit of answering HTTP 200 with an error inside are the real ones; the serial numbers, names and values are made up.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BUS = "AAAA0000BBBB1111CCCC2222DDDD3333"
BUS2 = "EEEE4444FFFF5555AAAA6666BBBB7777"
PIGEON = "Pigeon 2 vers. S"
TALON = "TalonFX vers. C"

SIGNALS = {
    PIGEON: [{"Id": 734, "Name": "VersionMajor", "Summary": "App Major Version number.", "Units": ""}, {"Id": 949, "Name": "Yaw", "Summary": "Yaw", "Units": "deg"},
             {"Id": 950, "Name": "Pitch", "Summary": "Pitch", "Units": "deg"}, {"Id": 951, "Name": "Roll", "Summary": "Roll", "Units": "deg"},
             {"Id": 959, "Name": "Temperature", "Summary": "Temp", "Units": "℃"}, {"Id": 973, "Name": "SupplyVoltage", "Summary": "V", "Units": "V"},
             {"Id": 1001, "Name": "Fault_Hardware", "Summary": "", "Units": ""}, {"Id": 1002, "Name": "StickyFault_Hardware", "Summary": "", "Units": ""},
             {"Id": 1003, "Name": "Fault_Undervoltage", "Summary": "", "Units": ""}, {"Id": 1004, "Name": "StickyFault_Undervoltage", "Summary": "", "Units": ""},
             {"Id": 1005, "Name": "StickyFault_DataAcquiredLate", "Summary": "", "Units": ""}],
    TALON: [{"Id": 10, "Name": "Position", "Summary": "", "Units": "rotations"}, {"Id": 11, "Name": "Velocity", "Summary": "", "Units": "rps"},
            {"Id": 12, "Name": "Fault_OverSupplyV", "Summary": "", "Units": ""}, {"Id": 13, "Name": "StickyFault_OverSupplyV", "Summary": "", "Units": ""}],
}


def device(name, model, ident, bus, label, version="26.70.0.0 (Phoenix 6)"):
    return {"BootloaderRev": "1.1", "CANbus": bus, "CANivoreDevName": label, "Compliancy": 21, "CurrentVers": version, "HardwareRev": "2.2", "ID": ident,
            "IsPROApplication": True, "IsPROLicensed": True, "LicenseCapacity": 5, "LicenseResponseCode": "OK", "LicenseSigs": ["1a98", "cb1e"],
            "Licenses": ["LIC-00-00000000-A", "LIC-00-00000000-B", "LIC-00-00000000-C"], "LicensesValid": True, "ManDate": "June 30, 2023", "Model": model, "Name": name,
            "SerialNo": "00000000000000000000000000000002", "SoftStatus": "Running Application.", "SupportsConfigs": True, "Vendor": "Cross The Road Electronics"}


class FakeDiag:
    """The state the fake serves, and a record of every request it got."""

    def __init__(self):
        self.devices = [device("0 - DT-GYRO", PIGEON, 0, BUS, "canfd"), device("5 - Turret", TALON, 5, BUS, "canfd"), device("5 - Spare", TALON, 5, BUS2, "can_s1")]
        self.values = {PIGEON: {734: 26.0, 949: 189.23126220703125, 950: 0.48, 951: 179.69, 959: 29.57, 973: 13.13, 1001: 0.0, 1002: 0.0, 1003: 0.0, 1004: 1.0, 1005: 1.0},
                       TALON: {10: 3.5, 11: 0.0, 12: 0.0, 13: 0.0}}
        self.stats = {BUS: {"ArbitrationLostCnt": 0, "BusErrorCnt": 0, "BusOffCnt": 0, "BusUtilPerc": 1.9999999552965164, "CANState": 0, "REC": 0, "RestartCnt": 0, "TEC": 0, "TxFullCnt": 0},
                      BUS2: {"ArbitrationLostCnt": 2, "BusErrorCnt": 7, "BusOffCnt": 1, "BusUtilPerc": 41.5, "CANState": 1, "REC": 3, "RestartCnt": 0, "TEC": 9, "TxFullCnt": 0}}
        self.configs = [{"Name": "Mount Pose", "Ordinal": 0, "Description": "", "Summary": "", "Configs": [
            {"Name": "Mount Pose Yaw", "Value": 90.0, "DefaultValue": 0.0, "Type": "double", "Units": "deg"},
            {"Name": "Mount Pose Pitch", "Value": 0.0, "DefaultValue": 0.0, "Type": "double", "Units": "deg"}]},
            {"Name": "Pigeon 2 Features", "Ordinal": 0, "Description": "", "Summary": "", "Configs": [
                {"Name": "Enable Compass", "Value": 1.0, "DefaultValue": 0.0, "Type": "bool", "Units": ""},
                {"Name": "Custom Param 0", "Value": 0.0, "DefaultValue": 0.0, "Type": "int", "Units": ""}]}]
        self.requests: list[dict[str, str]] = []
        self.mode = "ok"  # "html" (not a diag server), "notjson", "http500", "nogeneral"

    def actions(self):
        return [r["action"] for r in self.requests]

    def reply(self, params: dict[str, str]) -> tuple[int, bytes]:
        action = params.get("action", "")
        if self.mode == "http500":
            return 500, b"oops"
        if self.mode == "html":
            return 200, b"<html>router login</html>"
        if self.mode == "nogeneral":
            return 200, b'{"hello": 1}'

        def general(error=0, message="No Error"):
            return {"Action": action, "CANbus": params.get("canbus", ""), "Error": error, "ErrorMessage": message, "ID": int(params.get("id", 0)), "Model": params.get("model", "")}

        def not_found():
            return {"GeneralReturn": general(-120, "Specified device was not found. Verify the device is connected and powered, and that the CAN bus is terminated.")}

        model, ident, bus = params.get("model", ""), params.get("id", ""), params.get("canbus", "")
        match = next((d for d in self.devices if d["Model"] == model and str(d["ID"]) == ident and d["CANbus"] == bus), None)
        if action == "getversion":
            body = {"Compliancy": 21, "GeneralReturn": general(), "ReleaseInfo": "FRC2026", "SearchDirectory": "/tmp/ctre/", "System": "Systemcore", "Version": "2026.70.0 (Sep 18 2026, 00:15:58)"}
        elif action == "getdevices":
            body = {"BusUtilPerc": -1.0, "DeviceArray": self.devices, "GeneralReturn": general()}
        elif action == "getcanbusstats":
            body = {**self.stats[bus], "GeneralReturn": general()} if bus in self.stats else not_found()
        elif match is None and action in ("deviceinformation", "getsignals", "getsignalvalue", "getconfigv2", "blink"):
            body = not_found()
        elif action == "getsignals":
            body = {"GeneralReturn": general(), "Signals": SIGNALS[model]}
        elif action == "getsignalvalue":
            wanted = params.get("signals")
            body = {"GeneralReturn": general(-1002, "An invalid argument was passed into the function/VI, such as a null pointer.")} if not wanted else \
                   {"GeneralReturn": general(), "Values": {i: self.values[model][int(i)] for i in wanted.split(",")}}
        elif action == "deviceinformation":
            body = {"DeviceDetails": {"Frc Lock": True, "LED1 On Color": "Orange"}, "GeneralReturn": general()}
        elif action == "getconfigv2":
            body = {"Device": self.configs, "GeneralReturn": general(), "JsonVersion": 2} if model == PIGEON else {"Device": None, "GeneralReturn": general(-135, "This feature is not supported for this device model.")}
        elif action == "blink":
            body = {"GeneralReturn": general()}
        else:
            body = {"GeneralReturn": general(-1002, "An invalid argument was passed into the function/VI, such as a null pointer.")}
        return 200, json.dumps(body).encode("utf-8")


class Server:
    def __init__(self, fake: FakeDiag):
        outer = fake

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                params = {k: v[0] for k, v in parse_qs(urlparse(self.path).query, keep_blank_values=True).items()}
                outer.requests.append(params)
                status, body = outer.reply(params)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)  # shutdown() waits one poll: keep it short

    @property
    def port(self) -> int:
        return self.http.server_address[1]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.http.shutdown()
        self.http.server_close()
