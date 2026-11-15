"""WPILib type schemas: `structs.py` decodes `struct:<Name>`, `protos.py` decodes `proto:<file>`, `state.py` is the facade over
both. Where the schema bytes come from is up to each server (NT4: live `/.schema/` topics; logs: `structschema` entries).
Nothing here imports the rest of the package except `errors.py`.
"""
