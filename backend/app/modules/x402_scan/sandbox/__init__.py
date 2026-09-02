"""Not part of the running backend process.

This package exists only so scan.py's pure functions are importable for
unit tests. scan.py itself is only ever actually run inside the sandbox
container (see the Dockerfile ENTRYPOINT).
"""
