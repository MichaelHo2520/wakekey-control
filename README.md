# WakeKey control page

Static control page for WakeKey (ESP32-S3 USB wake keyboard) devices, served by GitHub Pages.

- Talks to the MQTT broker over WebSocket/TLS from the browser.
- Contains no credentials or broker address: both are entered on first use and,
  only if "remember" is ticked, kept in that browser's local storage.

- The device list (id + display name only, no passwords) is a retained message on
  `wakekey/_admin/devices`, readable only by `wk-admin` (devices are limited to their own
  `wakekey/<id>/#` by the broker ACL), so every browser sees the same list.
- Installable as a home-screen app (`manifest.json`, `icon.svg`).

Source of truth: `web/control.html` in the WakeKey firmware repository.
