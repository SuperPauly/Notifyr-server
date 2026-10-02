"""Generate bindings with the development environment's pinned protoc toolchain."""

from pathlib import Path

import grpc_tools
from grpc_tools import protoc

root = Path(__file__).resolve().parents[1]
raise SystemExit(
    protoc.main(
        [
            "protoc",
            f"-I{root / 'proto'}",
            f"-I{Path(grpc_tools.__file__).parent / '_proto'}",
            f"--python_out={root / 'src/notifyr'}",
            f"--pyi_out={root / 'src/notifyr'}",
            str(root / "proto/notifications.proto"),
        ]
    )
)
