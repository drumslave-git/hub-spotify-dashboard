"""Container entrypoint: make the data folder writable, then drop root.

A bind-mounted or freshly created data folder normally belongs to root on the
host, which the unprivileged app user cannot write to. So the container starts
as root, hands the folder to the app user, and runs the service as that user.
"""
import os
import sys

UID = GID = 1000
data = os.environ.get("DATA_DIR") or "/app/data"

if os.geteuid() == 0:
    os.makedirs(data, exist_ok=True)
    for folder, _dirs, files in os.walk(data):
        for path in [folder] + [os.path.join(folder, name) for name in files]:
            try:
                if os.stat(path).st_uid != UID:
                    os.chown(path, UID, GID)
            except OSError as err:
                print(f"entrypoint: cannot take ownership of {path}: {err}", flush=True)
    os.setgroups([])
    os.setgid(GID)
    os.setuid(UID)

if not os.access(data, os.W_OK):
    print(
        f"entrypoint: {data} is not writable by uid {os.geteuid()}; "
        "the Spotify sign-in will not survive a restart",
        flush=True,
    )

os.execvp(sys.argv[1], sys.argv[1:])
