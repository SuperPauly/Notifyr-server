"""Build and smoke-test an isolated non-root container; never deploy the Compose stack."""

import json
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def docker(*args: str) -> str:
    return subprocess.check_output(
        ["docker", *args], cwd=ROOT, text=True, stderr=subprocess.PIPE
    ).strip()  # noqa: S603


def smoke() -> None:
    suffix = uuid.uuid4().hex[:12]
    image, volume, container = (
        f"notifyr-smoke:{suffix}",
        f"notifyr-smoke-{suffix}",
        f"notifyr-{suffix}",
    )
    try:
        subprocess.run(["docker", "build", "-t", image, "."], cwd=ROOT, check=True)  # noqa: S603
        docker("volume", "create", volume)
        previous = None
        for expected_id in ("1", "2"):
            docker(
                "run",
                "-d",
                "--name",
                container,
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp:size=16m",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "-v",
                f"{volume}:/data",
                image,
            )
            assert docker("inspect", "--format", "{{.Config.User}}", container) == "10001:10001"
            deadline = time.monotonic() + 30
            while True:
                try:
                    docker(
                        "exec",
                        container,
                        "python",
                        "-c",
                        "import urllib.request; "
                        "urllib.request.urlopen('http://127.0.0.1:8080/ready',timeout=2)",
                    )
                    break
                except subprocess.CalledProcessError:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Container did not become ready") from None
                    time.sleep(0.25)
            # Provision and authenticate entirely inside the isolated container. No tokens
            # pass through process arguments, the host environment, logs or test output.
            code = """
import json, os, urllib.request, urllib.error, uuid
from notifyr.db import Database
from notifyr.config import Settings
db = Database(Settings().database)
token = db.provision('recipient','smoke-user',[])
publisher = db.provision('publisher','smoke-app',['smoke-user'])
base='http://127.0.0.1:8080'
def call(path, token=None, payload=None):
    headers={'Content-Type':'application/json'}
    if token: headers['Authorization']='Bearer '+token
    request=urllib.request.Request(base+path, headers=headers,
        data=json.dumps(payload).encode() if payload else None)
    return json.load(urllib.request.urlopen(request, timeout=3))
info=call('/v1/server-info')
try:
    call('/v1/me')
    raise AssertionError('Missing authentication was accepted')
except urllib.error.HTTPError as exc:
    assert exc.code == 401
assert call('/v1/me',token)['user_id']=='smoke-user'
n=call('/v1/notifications',publisher, {'request_id':str(uuid.uuid4()),
    'recipient_user_ids':['smoke-user'],'notification':{'title':'Smoke'}})
state=call('/v1/notifications/'+n['id']+'?from_server='+info['server_id'],token)
assert state['responded'] is False
assert os.getuid()==10001
print(json.dumps({'info':info,'id':n['id']}))
"""
            result = json.loads(docker("exec", container, "python", "-c", code))
            assert result["id"] == expected_id
            if previous:
                assert result["info"] == previous
            previous = result["info"]
            docker("stop", "--time", "30", container)
            assert docker("inspect", "--format", "{{.State.ExitCode}}", container) == "0"
            docker("rm", container)
        print(
            "Container smoke passed: non-root, readiness, auth, publish/get, clean shutdown, "
            "persistent UUID/VAPID/allocator across recreation; no live deployment changed."
        )
    finally:
        for args in [("rm", "-f", container), ("volume", "rm", volume), ("image", "rm", image)]:
            subprocess.run(
                ["docker", *args],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )  # noqa: S603


if __name__ == "__main__":
    smoke()
