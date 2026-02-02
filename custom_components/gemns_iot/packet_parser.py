"""Packet parser for Gemns™ IoT BLE devices with new packet format."""

import logging
import struct
from typing import Any

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

# Constants from the new packet format
COMPANY_ID = 0x0F9C  # Gemns™ IoT company ID
PACKET_LENGTH = 18  # Total packet length (HA BLE driver filters company ID)
ENCRYPTED_DATA_SIZE = 16

class GemnsPacketFlags:
    """Flags field parser for Gemns™ IoT packets."""

    def __init__(self, flags_byte: int):
        """Initialize packet flags parser."""
        self.encrypt_status = flags_byte & 0x01
        self.self_external_power = (flags_byte >> 1) & 0x01
        self.event_counter_lsb = (flags_byte >> 2) & 0x03
        self.payload_length = (flags_byte >> 4) & 0x0F

class GemnsEncryptedData:
    """Encrypted data structure for Gemns™ IoT packets."""

    def __init__(self, data: bytes, payload_length: int = 0):
        """Initialize encrypted data parser.
        
        Args:
            data: 16 bytes of encrypted data
            payload_length: Payload length from flags (0-15) to determine format
        """
        if len(data) != ENCRYPTED_DATA_SIZE:
            raise ValueError(f"Encrypted data must be {ENCRYPTED_DATA_SIZE} bytes")

        self.data_bytes = data
        self.payload_length = payload_length

        if payload_length < 10:
            self.src_id = data[0:3]
            self.nwk_id = data[3:5]
            self.fw_version = data[5]
            self.device_type = data[6:8]
            self.payload = data[8:16]
            self.has_fw_version = True
        else:
            self.src_id = data[0:3]
            self.nwk_id = data[3:5]
            self.fw_version = None
            self.device_type = data[5:6]
            self.payload = data[6:16]
            self.has_fw_version = False

        _LOGGER.info("ENCRYPTED DATA PARSING (payload_length=%d):", payload_length)
        _LOGGER.info("  Raw data: %s", data.hex())
        _LOGGER.info("  Raw data bytes: %s", [hex(b) for b in data])
        _LOGGER.info("  SRC ID (bytes 0-2): %s", self.src_id.hex())
        _LOGGER.info("  NWK ID (bytes 3-4): %s", self.nwk_id.hex())
        if self.has_fw_version:
            _LOGGER.info("  FW Version (byte 5): %d (0x%02X)", self.fw_version, self.fw_version)
            _LOGGER.info("  Device Type (bytes 6-7): %s", self.device_type.hex())
            _LOGGER.info("  Payload (bytes 8-15): %s", self.payload.hex())
        else:
            _LOGGER.info("  Device Type (byte 5): %s", self.device_type.hex())
            _LOGGER.info("  Payload (bytes 6-15): %s", self.payload.hex())

class GemnsPacket:
    """Parser for Gemns™ IoT BLE packets."""

    def __init__(self, raw_data: bytes):
        """Initialize packet parser with 18-byte packet (HA BLE driver filters company ID)."""
        if len(raw_data) < PACKET_LENGTH:
            raise ValueError(f"Packet data must be at least {PACKET_LENGTH} bytes")

        self.raw_data = raw_data
        self.company_id = COMPANY_ID
        self.flags = GemnsPacketFlags(raw_data[0])
        self.encrypted_data_bytes = raw_data[1:17]
        self.crc = raw_data[17]

        _LOGGER.info("PACKET STRUCTURE: Length=%d, Flags=0x%02X, CRC=0x%02X",
                    len(raw_data), raw_data[0], self.crc)

    def is_valid_company_id(self) -> bool:
        """Check if this is a Gemns™ IoT packet."""
        return self.company_id == COMPANY_ID

    def validate_crc(self) -> bool:
        """Validate CRC checksum."""
        company_id_bytes = struct.pack('<H', COMPANY_ID)
        full_packet = company_id_bytes + self.raw_data
        data_to_check = full_packet[:-1]
        calculated_crc = self._calculate_crc8(data_to_check)

        _LOGGER.info("CRC VALIDATION:")
        _LOGGER.info("  Company ID bytes: %s", company_id_bytes.hex())
        _LOGGER.info("  Raw data: %s", self.raw_data.hex())
        _LOGGER.info("  Full packet: %s", full_packet.hex())
        _LOGGER.info("  Data to check: %s", data_to_check.hex())
        _LOGGER.info("  Calculated CRC: 0x%02X", calculated_crc)
        _LOGGER.info("  Expected CRC: 0x%02X", self.crc)
        _LOGGER.info("  Match: %s", calculated_crc == self.crc)

        return calculated_crc == self.crc

    def _calculate_crc8(self, data: bytes) -> int:
        """Calculate CRC8 checksum using the same algorithm as the C code."""
        crc = 0x00

        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x07
                else:
                    crc <<= 1
                crc &= 0xFF

        return crc

    def decrypt_payload(self, decryption_key: bytes) -> dict[str, Any] | None:
        """Decrypt the encrypted data using AES-ECB."""
        try:
            if self.flags.encrypt_status == 1:
                decrypted_data = self.encrypted_data_bytes
            else:
                cipher = Cipher(
                    algorithms.AES(decryption_key),
                    modes.ECB(),
                    backend=default_backend()
                )
                decryptor = cipher.decryptor()
                decrypted_data = decryptor.update(self.encrypted_data_bytes) + decryptor.finalize()

            payload_length = self.flags.payload_length
            decrypted_packet = GemnsEncryptedData(decrypted_data, payload_length)

            _LOGGER.info("DECRYPTED DATA ANALYSIS:")
            _LOGGER.info("  Decrypted data length: %d", len(decrypted_data))
            _LOGGER.info("  Decrypted data hex: %s", decrypted_data.hex())
            _LOGGER.info("  Decrypted data bytes: %s", [hex(b) for b in decrypted_data])
            _LOGGER.info("  Payload length: %d (format: %s)", payload_length, 
                        "new (>=10)" if payload_length >= 10 else "old (<10)")

            firmware_version = None
            fw_byte = None
            if decrypted_packet.has_fw_version:
                fw_byte = decrypted_packet.fw_version
                major_version = (fw_byte >> 4) & 0x0F
                minor_version = fw_byte & 0x0F
                firmware_version = f"{major_version}.{minor_version}"

                _LOGGER.info("FIRMWARE VERSION PARSING: Raw byte=%d (0x%02X) -> Major=%d, Minor=%d -> Version='%s'",
                            fw_byte, fw_byte, major_version, minor_version, firmware_version)
            else:
                _LOGGER.info("FIRMWARE VERSION: Not available in new format (payload_length >= 10)")

            result = {
                'src_id': struct.unpack('<I', decrypted_packet.src_id + b'\x00')[0],
                'nwk_id': struct.unpack('<H', decrypted_packet.nwk_id)[0],
                'device_type': decrypted_packet.device_type,
                'payload': decrypted_packet.payload,
                'event_counter_lsb': self.flags.event_counter_lsb,
                'payload_length': self.flags.payload_length,
                'encrypt_status': self.flags.encrypt_status,
                'power_status': self.flags.self_external_power,
                'has_fw_version': decrypted_packet.has_fw_version,
            }

            if decrypted_packet.has_fw_version:
                result['fw_version'] = fw_byte
                result['firmware_version'] = firmware_version

            return result
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Decryption failed: %s", e)
            return None

    def parse_sensor_data(self, decrypted_data: dict[str, Any]) -> dict[str, Any]:
        """Parse sensor-specific data based on sensor type."""
        device_type_bytes = decrypted_data['device_type']
        payload_length = decrypted_data['payload_length']
        has_fw_version = decrypted_data.get('has_fw_version', True)
        
        if has_fw_version:
            device_type = struct.unpack('<H', device_type_bytes)[0]
        else:
            device_type = device_type_bytes[0]
        
        payload = decrypted_data['payload']

        _LOGGER.info("SENSOR DATA PARSING:")
        _LOGGER.info("  Device type bytes: %s", device_type_bytes.hex())
        _LOGGER.info("  Device type decimal: %d", device_type)
        _LOGGER.info("  Payload length: %d", payload_length)
        _LOGGER.info("  Payload: %s", payload.hex())

        sensor_data = {
            'device_type': device_type,
            'event_counter_lsb': decrypted_data['event_counter_lsb'],
            'payload_length': payload_length,
            'encrypt_status': decrypted_data['encrypt_status'],
            'power_status': decrypted_data['power_status'],
        }

        if payload_length >= 10:
            if len(payload) >= 10:
                event_counter = struct.unpack('<I', payload[0:3] + b'\x00')[0]
                event_type = payload[3]
                
                ax = struct.unpack('<h', payload[4:6])[0]
                ay = struct.unpack('<h', payload[6:8])[0]
                az = struct.unpack('<h', payload[8:10])[0]
                
                sensor_data.update({
                    'event_counter': event_counter,
                    'event_type': event_type,
                    'accelerometer': {
                        'ax': ax,
                        'ay': ay,
                        'az': az,
                    },
                })
                
                _LOGGER.info("  Event Counter: %d", event_counter)
                _LOGGER.info("  Event Type: %d", event_type)
                _LOGGER.info("  Accelerometer: ax=%d, ay=%d, az=%d", ax, ay, az)
            else:
                sensor_data.update({
                    'event_counter': 0,
                    'event_type': 0,
                    'accelerometer': {'ax': 0, 'ay': 0, 'az': 0},
                })
        else:
            if len(payload) >= 4:
                event_counter = struct.unpack('<I', payload[0:3] + b'\x00')[0]
                sensor_event = payload[3]
                
                sensor_data.update({
                    'event_counter': event_counter,
                    'sensor_event': sensor_event,
                })
            else:
                sensor_data.update({
                    'event_counter': 0,
                    'sensor_event': 0,
                })

        if device_type == 0:
            if payload_length >= 10:
                event_type = sensor_data.get('event_type', 0)
                sensor_data.update({
                    'button_pressed': event_type == 0,
                })
            else:
                sensor_data.update({
                    'button_pressed': sensor_data.get('sensor_event', 0) == 0,
                })

        elif device_type == 1:
            if payload_length >= 10:
                event_type = sensor_data.get('event_type', 0)
                sensor_data.update({
                    'button_pressed': event_type == 0,
                })
            else:
                sensor_data.update({
                    'button_pressed': sensor_data.get('sensor_event', 0) == 0,
                })

        elif device_type == 2:
            if payload_length >= 10:
                sensor_data.update({
                    'door_open': sensor_data.get('event_type', 0) == 1,
                })
            else:
                sensor_data.update({
                    'door_open': sensor_data.get('sensor_event', 0) == 1,
                })

        elif device_type == 4:
            if payload_length >= 10:
                sensor_data.update({
                    'leak_detected': sensor_data.get('event_type', 0) == 4,
                })
            else:
                sensor_data.update({
                    'leak_detected': sensor_data.get('sensor_event', 0) == 4,
                })

        elif device_type == 3:
            if payload_length >= 10:
                event_type = sensor_data.get('event_type', 0)
                sensor_data.update({
                    'switch_on': event_type == 2,
                    'switch_off': event_type == 3,
                })
            else:
                sensor_event = sensor_data.get('sensor_event', 0)
                sensor_data.update({
                    'switch_on': sensor_event == 2,
                    'switch_off': sensor_event == 3,
                })

        return sensor_data

def parse_gems_packet(manufacturer_data: bytes, decryption_key: bytes | None = None) -> dict[str, Any] | None:
    """Parse Gemns™ IoT packet from manufacturer data."""
    try:
        packet = GemnsPacket(manufacturer_data)

        if not packet.is_valid_company_id():
            return None

        if not packet.validate_crc():
            _LOGGER.warning("CRC validation failed for Gemns™ IoT packet")
            return None

        result = {
            'company_id': packet.company_id,
            'flags': {
                'encrypt_status': packet.flags.encrypt_status,
                'self_external_power': packet.flags.self_external_power,
                'event_counter_lsb': packet.flags.event_counter_lsb,
                'payload_length': packet.flags.payload_length,
            },
            'crc': packet.crc,
        }

        if decryption_key:
            decrypted_data = packet.decrypt_payload(decryption_key)
            if decrypted_data:
                result['decrypted_data'] = decrypted_data
                result['sensor_data'] = packet.parse_sensor_data(decrypted_data)

    except (ValueError, KeyError, AttributeError, TypeError) as e:
        _LOGGER.error("Failed to parse Gemns™ IoT packet: %s", e)
        return None
    else:
        return result


def parse_wepower_v2(manufacturer_data: bytes, decryption_key: bytes | None = None) -> dict[str, Any] | None:
    """Parse WePowerV2 manufacturer data.

    Expected format:
      - bytes 0-1: manufacturer id (little-endian)
      - bytes 2-16: 15-byte payload (plaintext)

    Payload mapping (0-based):
      - bytes 0-3: serial (uint32, LE)
      - byte 4: device type
      - bytes 5-8: event counter (uint32, LE)
      - byte 9: on/off (0=off,1=on)
    """
    try:
        if len(manufacturer_data) != 17:
            _LOGGER.debug("parse_wepower_v2: unexpected length %d (need 17)", len(manufacturer_data))
            return None

        manuf_id = struct.unpack('<H', manufacturer_data[0:2])[0]
        payload = manufacturer_data[2:17]  # 15 bytes

        serial = struct.unpack('<I', payload[0:4])[0]
        device_type = payload[4]
        event_counter = struct.unpack('<I', payload[5:9])[0]
        on_off = payload[9]

        result = {
            "manufacturer_id": manuf_id,
            "raw_payload": payload,
            "serial": serial,
            "device_type": device_type,
            "event_counter": event_counter,
            "on_off": int(on_off),
        }

        _LOGGER.info("parse_wepower_v2: parsed result: %s", result)
        return result
    except (ValueError, IndexError, struct.error, TypeError) as e:
        _LOGGER.error("parse_wepower_v2: failed to parse: %s", e)
        return None

# Backwards-compatible alias
parse_gemns_v2 = parse_wepower_v2
