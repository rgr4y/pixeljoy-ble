"""Constants for the PixelJoy integration."""

DOMAIN = "pixeljoy"

# Drop the BLE link after this long without a command, so the phone app can still connect.
IDLE_DISCONNECT_S = 60

SERVICE_SHOW_TEXT = "show_text"
SERVICE_SHOW_CLOCK = "show_clock"
SERVICE_SHOW_IMAGE = "show_image"
SERVICE_SHOW_GIF = "show_gif"
SERVICE_SYNC_TIME = "sync_time"
