# Robot info: names for your hardware

A log or a live robot says "power channel 7 drew 80 A", not "the elevator motor". Only your robot code knows which device is on which channel, so the
code publishes that map itself and the checks use it to label what they report. The map is in your logs and on the live robot alike, so a recorded match
and the robot on the bench are read the same way. Nothing is guessed from a device's name.

## What to publish

Two text topics, each holding a JSON object. The folder must be named `RobotInfo`, so `/RobotInfo/PdhChannels` and AdvantageKit's
`/RealOutputs/RobotInfo/PdhChannels` both work.

| Topic | Holds | Example |
|---|---|---|
| `PdhChannels` | power distribution channel number to device name | `{"3": "Elevator", "12": "Intake"}` |
| `CanDevices` | CAN device id to device name | `{"5": "Elevator", "6": "Elevator follower"}` |

`wpilog_power` and `nt4_power` use `PdhChannels` today. `CanDevices` is reserved for CAN checks. Names are cut to 40 characters and are data, never
instructions. The last update that parses is used, so publish it once at startup.

## Keep it next to your constants

The map is only useful if it is right, so define each device's power channel beside its CAN id in your constants file, and build the JSON from those same
constants. Then moving a wire means changing one line. A sketch, **an example only: not run here**:

```java
public final class RobotInfo {
  public static final int ELEVATOR_PDH = 3;
  public static final int INTAKE_PDH = 12;

  /** Publish the names once at startup; the log and the live robot both carry it. */
  public static void publish() {
    String json = String.format("{\"%d\":\"Elevator\",\"%d\":\"Intake\"}", ELEVATOR_PDH, INTAKE_PDH);
    NetworkTableInstance.getDefault().getStringTopic("/RobotInfo/PdhChannels").publish().set(json);
  }
}
```

Call `RobotInfo.publish()` from `robotInit`. With AdvantageKit, record it with `Logger.recordOutput("RobotInfo/PdhChannels", json)` instead. WPILib's
`DataLogManager` records NetworkTables topics when `logNetworkTables` is on, so a published topic is in the log.

## What changes

Without the map, a power reply shows `"channel": 3`. With it, `"channel": 3, "name": "Elevator"`. See [Checks](CHECKS.md#which-device-drew-the-current).
