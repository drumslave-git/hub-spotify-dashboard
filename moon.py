"""Moonrise and moonset, worked out here: the weather service gives the
sun's times but not the moon's.

The moon's position comes from the short series in the Astronomical Almanac
(good to about a third of a degree), which puts rise and set within a few
minutes of the published times.
"""
from __future__ import annotations

import datetime as dt
import math

J2000 = dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)


def _sin(degrees: float) -> float:
    return math.sin(math.radians(degrees))


def _cos(degrees: float) -> float:
    return math.cos(math.radians(degrees))


def altitude(when: dt.datetime, latitude: float, longitude: float) -> float:
    """Degrees above the point where the moon rises or sets: 0 at moonrise
    and moonset, above 0 while it is up. `when` is timezone-aware."""
    days = (when - J2000).total_seconds() / 86400
    t = days / 36525
    longitude_ecliptic = (
        218.32 + 481267.881 * t
        + 6.29 * _sin(135.0 + 477198.87 * t) - 1.27 * _sin(259.3 - 413335.36 * t)
        + 0.66 * _sin(235.7 + 890534.22 * t) + 0.21 * _sin(269.9 + 954397.74 * t)
        - 0.19 * _sin(357.5 + 35999.05 * t) - 0.11 * _sin(186.5 + 966404.03 * t)
    )
    latitude_ecliptic = (
        5.13 * _sin(93.3 + 483202.02 * t) + 0.28 * _sin(228.2 + 960400.89 * t)
        - 0.28 * _sin(318.3 + 6003.15 * t) - 0.17 * _sin(217.6 - 407332.21 * t)
    )
    parallax = (
        0.9508 + 0.0518 * _cos(135.0 + 477198.87 * t) + 0.0095 * _cos(259.3 - 413335.36 * t)
        + 0.0078 * _cos(235.7 + 890534.22 * t) + 0.0028 * _cos(269.9 + 954397.74 * t)
    )
    tilt = 23.439 - 0.013 * t

    # From the ecliptic to the sky's equator: right ascension and declination.
    x = _cos(latitude_ecliptic) * _cos(longitude_ecliptic)
    y = (_cos(tilt) * _cos(latitude_ecliptic) * _sin(longitude_ecliptic)
         - _sin(tilt) * _sin(latitude_ecliptic))
    z = (_sin(tilt) * _cos(latitude_ecliptic) * _sin(longitude_ecliptic)
         + _cos(tilt) * _sin(latitude_ecliptic))
    right_ascension = math.degrees(math.atan2(y, x))
    declination = math.degrees(math.asin(z))

    sidereal = 280.46061837 + 360.98564736629 * days + longitude
    hour_angle = sidereal - right_ascension
    height = math.degrees(math.asin(
        _sin(latitude) * _sin(declination)
        + _cos(latitude) * _cos(declination) * _cos(hour_angle)
    ))
    # The moon's top edge touches the horizon, seen from the ground (parallax)
    # and through the air (refraction).
    return height - (0.7275 * parallax - 0.5667)


def passes(latitude: float, longitude: float, start: dt.datetime, days: int,
           utc_offset: int) -> list[dict]:
    """Each time the moon rises and sets again within `days` from `start`
    (timezone-aware), as local times "YYYY-MM-DDTHH:MM" like the weather's
    sunrise and sunset; `utc_offset` is the local offset in seconds."""
    step = dt.timedelta(minutes=10)
    end = start + dt.timedelta(days=days)
    events: list[tuple[dt.datetime, bool]] = []   # (when, True for a rise)
    t0, a0 = start, altitude(start, latitude, longitude)
    while t0 < end:
        t1 = t0 + step
        a1 = altitude(t1, latitude, longitude)
        if (a0 > 0) != (a1 > 0):
            low, high = t0, t1
            for _ in range(10):   # down to under a second
                middle = low + (high - low) / 2
                if (altitude(middle, latitude, longitude) > 0) == (a0 > 0):
                    low = middle
                else:
                    high = middle
            events.append((high, a1 > 0))
        t0, a0 = t1, a1

    local = lambda when: (when + dt.timedelta(seconds=utc_offset + 30)).strftime("%Y-%m-%dT%H:%M")
    out = []
    for (when, rise), (after, rise_after) in zip(events, events[1:]):
        if rise and not rise_after:
            out.append({"rise": local(when), "set": local(after)})
    return out
