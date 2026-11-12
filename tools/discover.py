"""List Google Cast devices on the local network."""
import time

import pychromecast
import zeroconf

zconf = zeroconf.Zeroconf()
browser = pychromecast.CastBrowser(
    pychromecast.SimpleCastListener(lambda uuid, service: None), zconf
)
browser.start_discovery()
time.sleep(20)

devices = sorted(browser.devices.values(), key=lambda d: d.friendly_name)
if not devices:
    print("No Cast devices found.")
for d in devices:
    print(
        f"{d.friendly_name!r:32} model={d.model_name!r:24} "
        f"type={d.cast_type} host={d.host}:{d.port} uuid={d.uuid}"
    )

browser.stop_discovery()
zconf.close()
