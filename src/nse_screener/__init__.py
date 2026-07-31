"""Two-stage NSE equity screener.

Stage 1: Chartink momentum/volume scan (technical).
Stage 2: seven weighted fundamental parameters from a manual Screener.in export,
         with sector tailwind reported separately.

See the build spec for the rules; every threshold lives in config/settings.json.
"""

__version__ = "0.1.0"
