"""Systemcore server: find the robot controller on the network, read its health, fetch support bundles and manage its logs.
Most of it needs no login (plain HTTP services and a UDP discovery responder). SSH, with a key this server owns and never a
password the model sees, adds file access, log pruning and a few shell-only readings.
"""
