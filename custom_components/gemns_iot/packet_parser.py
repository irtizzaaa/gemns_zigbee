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

    def __init__(self, data: bytes):
        """Initialize encrypted data parser."""
        if len(data) != ENCRYPTED_DATA_SIZE:
            raise ValueError(f"Encrypted data must be {ENCRYPTED_DATA_SIZE} bytes")

        # Store the raw data bytes
        self.data_bytes = data

        # Parse the encrypted data according to the packet format
        # Note: This structure might be different for decrypted vs encrypted data
        self.src_id = data[0:3]  # 3 bytes - Source ID (truncated serial number)
        self.nwk_id = data[3:5]  # 2 bytes - Network ID
        self.fw_version = data[5]  # 1 byte - Firmware version
        self.device_type = data[6:8]  # 2 bytes - Device type
        self.payload = data[8:16]  # 8 bytes - Custom payload

        _LOGGER.info("ENCRYPTED DATA PARSING:")
        _LOGGER.info("  Raw data: %s", data.hex())
        _LOGGER.info("  Raw data bytes: %s", [hex(b) for b in data])
        _LOGGER.info("  SRC ID (bytes 0-2): %s", self.src_id.hex())
        _LOGGER.info("  NWK ID (bytes 3-4): %s", self.nwk_id.hex())
        _LOGGER.info("  FW Version (byte 5): %d (0x%02X)", self.fw_version, self.fw_version)
        _LOGGER.info("  Device Type (bytes 6-7): %s", self.device_type.hex())
        _LOGGER.info("  Payload (bytes 8-15): %s", self.payload.hex())

class GemnsPacket:
    """Parser for Gemns™ IoT BLE packets."""

    def __init__(self, raw_data: bytes):
        """Initialize packet parser with 18-byte packet (HA BLE driver filters company ID)."""
        if len(raw_data) < PACKET_LENGTH:
            raise ValueError(f"Packet data must be at least {PACKET_LENGTH} bytes")

        self.raw_data = raw_data
        # Packet structure after HA BLE driver filters company ID:
        # Flags (1 byte) + Encrypted Data (16 bytes) + CRC (1 byte) = 18 bytes
        self.company_id = COMPANY_ID  # Gemns™ IoT company ID (filtered by HA)
        self.flags = GemnsPacketFlags(raw_data[0])  # 1 byte flags
        self.encrypted_data = GemnsEncryptedData(raw_data[1:17])  # 16 bytes encrypted data
        self.crc = raw_data[17]  # 1 byte CRC (position 17, not 16!)

        _LOGGER.info("PACKET STRUCTURE: Length=%d, Flags=0x%02X, CRC=0x%02X",
                    len(raw_data), raw_data[0], self.crc)

    def is_valid_company_id(self) -> bool:
        """Check if this is a Gemns™ IoT packet."""
        return self.company_id == COMPANY_ID

    def validate_crc(self) -> bool:
        """Validate CRC checksum."""
        # Reconstruct the full packet for CRC calculation
        # Original packet: Company ID (2) + Flags (1) + Encrypted Data (16) + CRC (1) = 20 bytes
        # We have: Flags (1) + Encrypted Data (16) + CRC (1) = 18 bytes
        # Need to add Company ID back for CRC calculation

        company_id_bytes = struct.pack('<H', COMPANY_ID)  # 0x0F9C as little-endian bytes
        full_packet = company_id_bytes + self.raw_data  # Add company ID back

        # Calculate CRC over all data except the last byte (CRC field)
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
        # CRC-8 with polynomial 0x07, initial value 0x00, no reflection
        # This matches the C implementation: crc8(data, len, 0x07, 0x00, false)
        crc = 0x00  # Initial value

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
            # Check if decryption is needed based on encrypt_status flag
            # 0 = encrypted, 1 = not encrypted (clear text)
            if self.flags.encrypt_status == 1:
                # Data is not encrypted, return as-is
                decrypted_data = self.encrypted_data.data_bytes
            else:
                # Data is encrypted (encrypt_status == 0), decrypt it
                cipher = Cipher(
                    algorithms.AES(decryption_key),
                    modes.ECB(),
                    backend=default_backend()
                )
                decryptor = cipher.decryptor()
                decrypted_data = decryptor.update(self.encrypted_data.data_bytes) + decryptor.finalize()

            # Parse decrypted data
            decrypted_packet = GemnsEncryptedData(decrypted_data)

            _LOGGER.info("DECRYPTED DATA ANALYSIS:")
            _LOGGER.info("  Decrypted data length: %d", len(decrypted_data))
            _LOGGER.info("  Decrypted data hex: %s", decrypted_data.hex())
            _LOGGER.info("  Decrypted data bytes: %s", [hex(b) for b in decrypted_data])

            # Format firmware version: single byte -> X.Y format
            # First 4 bits = major version (left of decimal)
            # Last 4 bits = minor version (right of decimal)
            fw_byte = decrypted_packet.fw_version
            major_version = (fw_byte >> 4) & 0x0F
            minor_version = fw_byte & 0x0F
            firmware_version = f"{major_version}.{minor_version}"

            _LOGGER.info("FIRMWARE VERSION PARSING: Raw byte=%d (0x%02X) -> Major=%d, Minor=%d -> Version='%s'",
                        fw_byte, fw_byte, major_version, minor_version, firmware_version)

            return {
                'src_id': struct.unpack('<I', decrypted_packet.src_id + b'\x00')[0],  # Convert 3 bytes to 32-bit int
                'nwk_id': struct.unpack('<H', decrypted_packet.nwk_id)[0],  # Convert to integer
                'fw_version': fw_byte,  # Keep raw byte for debugging
                'firmware_version': firmware_version,  # Formatted version string
                'device_type': decrypted_packet.device_type,  # Keep as bytes
                'payload': decrypted_packet.payload,  # Keep as bytes
                'event_counter_lsb': self.flags.event_counter_lsb,
                'payload_length': self.flags.payload_length,
                'encrypt_status': self.flags.encrypt_status,
                'power_status': self.flags.self_external_power,
            }
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            _LOGGER.error("Decryption failed: %s", e)
            return None

    def parse_sensor_data(self, decrypted_data: dict[str, Any]) -> dict[str, Any]:
        """Parse sensor-specific data based on sensor type."""
        # Parse device_type as little-endian 16-bit integer from bytes
        device_type_bytes = decrypted_data['device_type']  # Already bytes
        device_type = struct.unpack('<H', device_type_bytes)[0]  # Little-endian unsigned short
        payload = decrypted_data['payload']  # Already bytes

        _LOGGER.info("SENSOR DATA PARSING:")
        _LOGGER.info("  Device type bytes: %s", device_type_bytes.hex())
        _LOGGER.info("  Device type decimal: %d", device_type)
        _LOGGER.info("  Payload: %s", payload.hex())

        sensor_data = {
            'device_type': device_type,
            'event_counter_lsb': decrypted_data['event_counter_lsb'],
            'payload_length': decrypted_data['payload_length'],
            'encrypt_status': decrypted_data['encrypt_status'],
            'power_status': decrypted_data['power_status'],
        }

        # Parse based on sensor type (matching device_type_t enum)
        if device_type == 4:  # DEVICE_TYPE_LEAK_SENSOR
            if len(payload) >= 4:
                # Event Counter (3 bytes) + Sensor Event Report (1 byte)
                event_counter = struct.unpack('<I', payload[0:3] + b'\x00')[0]  # Pad to 4 bytes
                sensor_event = payload[3]

                sensor_data.update({
                    'event_counter': event_counter,
                    'sensor_event': sensor_event,
                    'leak_detected': sensor_event == 4,  # EVENT_TYPE_LEAK_DETECTED = 4
                })
            else:
                # No payload data - device is off/no leak
                sensor_data.update({
                    'event_counter': 0,
                    'sensor_event': 0,
                    'leak_detected': False,  # No payload means no leak detected
                })

        elif device_type == 2:  # DEVICE_TYPE_VIBRATION_MONITOR
            if len(payload) >= 4:
                event_counter = struct.unpack('<I', payload[0:3] + b'\x00')[0]
                sensor_event = payload[3]

                sensor_data.update({
                    'event_counter': event_counter,
                    'sensor_event': sensor_event,
                    'vibration_detected': sensor_event == 1,  # EVENT_TYPE_VIBRATION = 1
                })
            else:
                sensor_data.update({
                    'event_counter': 0,
                    'sensor_event': 0,
                    'vibration_detected': False,
                })

        elif device_type == 3:  # DEVICE_TYPE_TWO_WAY_SWITCH
            if len(payload) >= 4:
                event_counter = struct.unpack('<I', payload[0:3] + b'\x00')[0]
                sensor_event = payload[3]

                sensor_data.update({
                    'event_counter': event_counter,
                    'sensor_event': sensor_event,
                    'switch_on': sensor_event == 3,  # EVENT_TYPE_BUTTON_ON = 3
                })
            else:
                sensor_data.update({
                    'event_counter': 0,
                    'sensor_event': 0,
                    'switch_on': False,
                })

        elif device_type in [0, 1]:  # DEVICE_TYPE_LEGACY, DEVICE_TYPE_BUTTON
            if len(payload) >= 4:
                event_counter = struct.unpack('<I', payload[0:3] + b'\x00')[0]
                sensor_event = payload[3]

                sensor_data.update({
                    'event_counter': event_counter,
                    'sensor_event': sensor_event,
                    'button_pressed': sensor_event == 0,  # EVENT_TYPE_BUTTON_PRESS = 0
                })
            else:
                sensor_data.update({
                    'event_counter': 0,
                    'sensor_event': 0,
                    'button_pressed': False,
                })

        return sensor_data

def parse_gems_packet(manufacturer_data: bytes, decryption_key: bytes | None = None) -> dict[str, Any] | None:
    """Parse Gemns™ IoT packet from manufacturer data."""
    try:
        packet = GemnsPacket(manufacturer_data)

        if not packet.is_valid_company_id():
            return None

        # Validate CRC before processing
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

        # If decryption key is provided, decrypt the data
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
