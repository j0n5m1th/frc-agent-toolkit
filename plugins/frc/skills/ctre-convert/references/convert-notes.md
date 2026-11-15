# Convert notes (Owlet and Passerine)

Read this when a conversion fails, or you need to explain why a step is done. Facts come from `docs/CTRE.md`. "Seen" means seen on a real run; "not checked" means nobody has tried it.

## Hoot to wpilog (Owlet)

- Only an Owlet of exactly the log's compliancy reads it right. Seen on a compliancy 19 log: Owlet 21 and 20 refused it, 19 read all 2,124 signals, and 18 exited with success but listed only 36. The server never uses a neighbouring level.
- Signal names look like `Phoenix6/<bus>/<device>/<Signal>`. `owlet_signals` shows 20 per page, plus the biggest device groups.
- A bad signal id does not stop Owlet (seen, 26.2.0 and 26.70.0). It prints `Invalid signal` and exports every signal: over 1 GB for a 54 MB log. So the server checks every id first and never runs Owlet with an unchecked `-s` list.
- Owlet overwrites its output. The server refuses and asks for another `out` name.
- Owlet can say success without writing a file. The server checks the file exists.
- Seen: 4 converted signals opened with `wpilog_open` as a 288 second log.
- Not checked: how hoot timestamps line up with a robot-made log. Do not compare the two by time without confirming it.
- Files are read from and written to `OWLET_DIR`.

## MIDI to chrp (Passerine)

- Files are read from and written to `PASSERINE_DIR`. `passerine_list` with `kind=chrp` lists converted files.
- Seen with passerine 1.0.0: it silently overwrites an existing output, and a file that is not MIDI prints an error and exits 1. So the server names the output itself (or uses your `out`), refuses to overwrite, checks for the MIDI header (`MThd`) first, checks the output is not empty, and deletes a half-written file.
- Loading the file is the user's step. CTRE's example uses `Orchestra.loadMusic("name.chrp")`.
- Not checked: whether a song sounds right (needs a motor), and whether a 1.0.0 file plays with Phoenix 2026. CTRE lists passerine 1.0.0 only under the 2027 alpha channel, and `ctre_status` shows a note.

## Errors

| You see | It means |
|---|---|
| `No Owlet is installed` | Turn on "Allow installing CTRE tools", then ask for `ctre_install` with `tool=owlet`. |
| `is a compliancy 21 log ... ctre_install tool=owlet compliancy=21` | No Owlet of exactly that level. Install the one named; a newer or older one will not do. |
| `no_build` | CTRE has no build of that tool for this computer. |
| `already exists next to ...` | Nothing is overwritten. Choose another `out` name. |
| "Not a MIDI file" | Ask the user for a real MIDI export. |
