#include "ble_bridge.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <BLESecurity.h>
#include <Arduino.h>
#include <string.h>

// Nordic UART Service UUIDs — every BLE serial example uses these, so
// existing tools (nRF Connect, bluefy, Web Bluetooth examples) can talk to
// us without custom UUIDs.
#define NUS_SERVICE_UUID "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define NUS_RX_UUID      "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
#define NUS_TX_UUID      "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

// Incoming bytes are buffered in a simple ring for bleRead()/bleAvailable().
// Sized to hold a transcript snapshot JSON plus headroom; the GATT layer
// will flow-control if we fall behind.
static const size_t RX_CAP = 2048;
static uint8_t  rxBuf[RX_CAP];
static volatile size_t rxHead = 0;
static volatile size_t rxTail = 0;

static BLEServer*         server = nullptr;
static BLECharacteristic* txChar = nullptr;
static BLECharacteristic* rxChar = nullptr;
static volatile bool      connected = false;
static volatile uint16_t  mtu = 23;

// 6-digit passkey generated fresh at boot. Displayed on Serial so the
// user can type it into the macOS pairing dialog. Stays the same across
// the lifetime of this boot — bonds persist in NVS so a paired host
// only ever sees the passkey on its very first connection. Erasing the
// bond (System Settings -> Bluetooth -> Forget on macOS, or
// `pio run -t erase` on the stick) forces a re-pair against whatever
// passkey the *next* boot generates.
static uint32_t pairingPasskey = 0;

static void rxPush(const uint8_t* p, size_t n) {
  for (size_t i = 0; i < n; i++) {
    size_t next = (rxHead + 1) % RX_CAP;
    if (next == rxTail) return;  // full — drop (upstream should keep up)
    rxBuf[rxHead] = p[i];
    rxHead = next;
  }
}

class RxCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* c) override {
    std::string v = c->getValue();
    if (!v.empty()) rxPush((const uint8_t*)v.data(), v.size());
  }
};

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* s) override {
    connected = true;
    // Negotiated MTU is reported via BLEDevice's global; we also keep
    // advertising after connect so other clients can find us again if
    // the current one drops.
    Serial.println("[ble] connected");
  }
  void onDisconnect(BLEServer* s) override {
    connected = false;
    mtu = 23;
    Serial.println("[ble] disconnected");
    // Restart advertising so the next client can find us.
    BLEDevice::startAdvertising();
  }
  void onMtuChanged(BLEServer*, esp_ble_gatts_cb_param_t* param) override {
    mtu = param->mtu.mtu;
    Serial.printf("[ble] mtu=%u\n", mtu);
  }
};

// Passkey pairing (Secure Connections + MITM + bond). The stick is a
// DisplayOnly device: at boot we generate a random 6-digit passkey and
// print it on Serial; macOS prompts the user to type that passkey into
// its pairing dialog; the BLE stack derives session keys from it and
// persists the bond in NVS. After the first pair the user never sees
// the dialog again until either side forgets the bond.
//
// onPassKeyRequest / onConfirmPIN are unreachable under DisplayOnly +
// host-as-Keyboard, but the BLESecurityCallbacks interface is
// pure-virtual so they still need definitions.
class SecCallbacks : public BLESecurityCallbacks {
  uint32_t onPassKeyRequest() override { return pairingPasskey; }
  void     onPassKeyNotify(uint32_t pk) override {
    // Some stack builds invoke this with the active static passkey
    // when pairing kicks off. Re-print it so the user can find it
    // even if they missed the boot banner.
    Serial.printf("[ble] pairing passkey (notify): %06u\n", (unsigned)pk);
  }
  bool     onConfirmPIN(uint32_t)      override { return true; }
  bool     onSecurityRequest()         override { return true; }
  void     onAuthenticationComplete(esp_ble_auth_cmpl_t cmpl) override {
    if (cmpl.success) {
      Serial.printf("[ble] paired & bonded with %02x:%02x:%02x:%02x:%02x:%02x\n",
                    cmpl.bd_addr[0], cmpl.bd_addr[1], cmpl.bd_addr[2],
                    cmpl.bd_addr[3], cmpl.bd_addr[4], cmpl.bd_addr[5]);
    } else {
      // fail_reason is an esp_ble_auth_fail_rsn_t; common values:
      //   0x05 PIN/Key missing  0x06 OOB not available  0x3D bond lost
      Serial.printf("[ble] auth FAILED reason=0x%02x — try erasing the macOS\n"
                    "      bond (System Settings -> Bluetooth -> Forget) or\n"
                    "      pio run -t erase to clear the stick's bond store\n",
                    cmpl.fail_reason);
    }
  }
};

void bleInit(const char* deviceName) {
  BLEDevice::init(deviceName);
  // Request the biggest MTU we can get. macOS negotiates to 185 typically.
  BLEDevice::setMTU(517);

  // Generate a fresh 6-digit passkey for this boot. esp_random() is
  // seeded by the hardware RNG once Wi-Fi/BT radios are up, which
  // BLEDevice::init() guarantees, so this is genuinely unpredictable
  // (not Arduino's PRNG). Modulo 1_000_000 has a tiny bias toward
  // lower numbers but it's negligible against a 32-bit source.
  pairingPasskey = esp_random() % 1000000;
  Serial.println();
  Serial.println("[ble] ============================================");
  Serial.printf ("[ble]   PAIRING PASSKEY:  %06u\n", (unsigned)pairingPasskey);
  Serial.println("[ble]   Enter this on macOS when it prompts you to");
  Serial.println("[ble]   pair with the Nibblet stick.");
  Serial.println("[ble] ============================================");
  Serial.println();

  // Security must be configured before the service is started so the
  // GATT permissions are honored on the first incoming connect. We use
  // Secure Connections + MITM + bond with DisplayOnly capability so the
  // host has to enter the passkey above instead of pairing silently.
  BLEDevice::setSecurityCallbacks(new SecCallbacks());
  BLESecurity* sec = new BLESecurity();
  sec->setAuthenticationMode(ESP_LE_AUTH_REQ_SC_MITM_BOND);
  sec->setCapability(ESP_IO_CAP_OUT);
  sec->setKeySize(16);
  sec->setInitEncryptionKey(ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK);
  sec->setRespEncryptionKey(ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK);

  // Hand the random passkey to the SMP layer. This must happen after
  // BLEDevice::init() (the GAP/SMP module needs to be alive) and
  // before advertising starts. Going through the raw esp_ble API
  // sidesteps version drift in BLESecurity::setStaticPIN.
  uint32_t pkParam = pairingPasskey;
  esp_ble_gap_set_security_param(ESP_BLE_SM_SET_STATIC_PASSKEY,
                                 &pkParam, sizeof(uint32_t));

  server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  BLEService* svc = server->createService(NUS_SERVICE_UUID);

  txChar = svc->createCharacteristic(
    NUS_TX_UUID,
    BLECharacteristic::PROPERTY_NOTIFY
  );
  // Mark the NOTIFY characteristic as encryption-required. The CCCD
  // descriptor (0x2902) needs the same permission or macOS would be
  // allowed to subscribe without pairing first.
  txChar->setAccessPermissions(ESP_GATT_PERM_READ_ENCRYPTED |
                                ESP_GATT_PERM_WRITE_ENCRYPTED);
  BLE2902* cccd = new BLE2902();
  cccd->setAccessPermissions(ESP_GATT_PERM_READ_ENCRYPTED |
                              ESP_GATT_PERM_WRITE_ENCRYPTED);
  txChar->addDescriptor(cccd);

  rxChar = svc->createCharacteristic(
    NUS_RX_UUID,
    BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
  );
  rxChar->setAccessPermissions(ESP_GATT_PERM_READ_ENCRYPTED |
                                ESP_GATT_PERM_WRITE_ENCRYPTED);
  rxChar->setCallbacks(new RxCallbacks());

  svc->start();

  BLEAdvertising* adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(NUS_SERVICE_UUID);
  adv->setScanResponse(true);
  adv->setMinPreferred(0x06);   // iOS-friendly connection interval
  adv->setMaxPreferred(0x12);
  BLEDevice::startAdvertising();
  Serial.printf("[ble] advertising as '%s' (encrypted, passkey pairing)\n",
                deviceName);
}

bool bleConnected() { return connected; }

size_t bleAvailable() {
  return (rxHead + RX_CAP - rxTail) % RX_CAP;
}

int bleRead() {
  if (rxHead == rxTail) return -1;
  int b = rxBuf[rxTail];
  rxTail = (rxTail + 1) % RX_CAP;
  return b;
}

size_t bleWrite(const uint8_t* data, size_t len) {
  if (!connected || !txChar) return 0;
  // ATT notify payload is limited to (MTU - 3). macOS negotiates 185, so
  // the 182-byte chunk works there; use the live mtu so a peer that caps
  // at the 23-byte default doesn't get truncated notifies.
  size_t chunk = mtu > 3 ? mtu - 3 : 20;
  if (chunk > 180) chunk = 180;
  size_t sent = 0;
  while (sent < len) {
    size_t n = len - sent;
    if (n > chunk) n = chunk;
    txChar->setValue((uint8_t*)(data + sent), n);
    txChar->notify();
    sent += n;
    // Small yield so the BLE stack flushes before the next chunk.
    delay(4);
  }
  return sent;
}
