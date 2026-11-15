"""ntcore-facing operations that `session.py` composes: reading, waiting, writer, publishing, publisher_registry, topic_listing.

They may use the shared root helpers but never session, tools or server. This package holds no code: import a name from the
module that owns it (`from .access.reading import ReadMiss`).
"""
