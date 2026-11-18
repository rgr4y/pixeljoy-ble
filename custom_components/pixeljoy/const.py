"""Constants for the PixelJoy integration."""

DOMAIN = "pixeljoy"

# Drop the BLE link after this long without a command, so the phone app can still connect.
IDLE_DISCONNECT_S = 60

SERVICE_SHOW_TEXT = "show_text"
SERVICE_SHOW_CLOCK = "show_clock"
SERVICE_SHOW_IMAGE = "show_image"
SERVICE_SHOW_GIF = "show_gif"
SERVICE_SYNC_TIME = "sync_time"

# Clock button options (same names as the show_clock service fields); style is 1-based.
CONF_CLOCK_STYLE = "style"
CONF_CLOCK_24H = "hour_24"
CONF_CLOCK_DATE = "show_date"
CONF_CLOCK_COLOR = "color"
CLOCK_DEFAULTS = {CONF_CLOCK_STYLE: "1", CONF_CLOCK_24H: True, CONF_CLOCK_DATE: False, CONF_CLOCK_COLOR: [255, 255, 255]}

# Clock style previews (the vendor app's 32x16 picker GIFs) served for the options dialog.
CLOCK_PREVIEW_URL = "/pixeljoy_static/clock"
