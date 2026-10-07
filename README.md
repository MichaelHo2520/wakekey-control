# WakeKey control page

Static control page for WakeKey (ESP32-S3 USB wake keyboard) devices, served by GitHub Pages.

- Talks to the MQTT broker over WebSocket/TLS from the browser.
- Contains no credentials or broker address: both are entered on first use and,
  only if "remember" is ticked, kept in that browser's local storage.

Source of truth: `web/control.html` in the WakeKey firmware repository.
