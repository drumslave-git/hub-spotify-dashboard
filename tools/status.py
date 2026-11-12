"""Print the Nest Hub's current Cast status.

Usage: python tools/status.py <hub-ip>   (or set HUB_HOST)
"""
import os
import sys
import time

import pychromecast

HOST = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("HUB_HOST")
if not HOST:
    raise SystemExit(__doc__)

cast = pychromecast.get_chromecast_from_host((HOST, 8009, None, None, None))
cast.wait(timeout=10)
time.sleep(1)

info = cast.cast_info
st = cast.status
print("name        :", info.friendly_name)
print("model       :", info.model_name, "/", info.manufacturer)
print("app         :", st.display_name, f"(id={st.app_id})")
print("status text :", st.status_text)
print("idle screen :", st.is_stand_by, "/ active input:", st.is_active_input)
print("volume      :", round(st.volume_level, 2), "muted" if st.volume_muted else "")
print("namespaces  :", st.namespaces)
cast.disconnect()
