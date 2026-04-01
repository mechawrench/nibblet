#pragma once
#include <stdint.h>
#include <stddef.h>

// Nordic UART Service-compatible BLE bridge. Clients (browser Web
// Bluetooth, noble, etc.) subscribe to NUS to talk to the Stick exactly
// like a serial port.
//
// Service UUID  6e400001-b5a3-f393-e0a9-e50e24dcca9e
// RX char       6e400002-b5a3-f393-e0a9-e50e24dcca9e   (client → stick, WRITE)
// TX char       6e400003-b5a3-f393-e0a9-e50e24dcca9e   (stick → client, NOTIFY)
//
// Writes from the client are line-buffered and dispatched through the
// same _applyJson path that USB/BT-Classic use. Replies (acks, status
// snapshots) are written via bleWrite() and chunked to the negotiated MTU.

void bleInit(const char* deviceName);
bool bleConnected();
size_t bleAvailable();
int bleRead();
size_t bleWrite(const uint8_t* data, size_t len);
