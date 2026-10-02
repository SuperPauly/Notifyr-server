"""Local administration only. Token values are printed once to the operator."""

import argparse
import json
import os
import sqlite3
from pathlib import Path

from notifyr.config import Settings
from notifyr.db import Database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    recipient = sub.add_parser("recipient")
    recipient.add_argument("user")
    publisher = sub.add_parser("publisher")
    publisher.add_argument("app")
    publisher.add_argument("--recipient", action="append", default=[])
    grant = sub.add_parser("grant")
    grant.add_argument("app")
    grant.add_argument("user")
    revoke = sub.add_parser("revoke")
    revoke.add_argument("token_hash", help="SHA-256 token hash from local token inventory")
    sub.add_parser("tokens")
    sub.add_parser("push-status")
    backup = sub.add_parser("backup")
    backup.add_argument("destination", type=Path)
    args = parser.parse_args()
    db = Database(Settings().database)
    db.initialize()
    if args.command in {"recipient", "publisher"}:
        print(
            db.provision(
                args.command,
                args.user if args.command == "recipient" else args.app,
                [] if args.command == "recipient" else args.recipient,
            )
        )
    elif args.command == "backup":
        fd = os.open(args.destination, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        source = db.connect()
        target = sqlite3.connect(args.destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        print("Consistent database backup created (includes identity, VAPID and queue)")
    elif args.command == "init":
        print(json.dumps(db.info()))
    else:
        with db.transaction() as con:
            if args.command == "grant":
                con.execute("INSERT OR IGNORE INTO grants VALUES (?,?)", (args.app, args.user))
            elif args.command == "revoke":
                cur = con.execute("UPDATE tokens SET revoked=1 WHERE hash=?", (args.token_hash,))
                print(f"Revoked {cur.rowcount} token(s)")
            elif args.command == "tokens":
                print(
                    json.dumps(
                        [
                            dict(row)
                            for row in con.execute("SELECT hash,scope,user,app,revoked FROM tokens")
                        ]
                    )
                )
            elif args.command == "push-status":
                print(
                    json.dumps(
                        [
                            dict(row)
                            for row in con.execute(
                                "SELECT status,diagnostic,count(*) AS count FROM jobs "
                                "GROUP BY status,diagnostic"
                            )
                        ]
                    )
                )


if __name__ == "__main__":
    main()
