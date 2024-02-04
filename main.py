"""
EVBee Charger BLE Comms
-------------

Connect to EVBee BLE charger, and set it to start sending info

04/02/2024

"""

import argparse
import asyncio
import logging
import binascii
import time

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic

#logger = logging.getLogger(__name__)

# EVBee UUIDs
evbee_name         = "EVbee_6E0D"
evbee_service_uuid = "55535343-FE7D-4AE5-8FA9-9FAFD205E455"
evbee_write_uuid   = "48535343-1E4D-4BD9-BA61-23C647249616"
evbee_notify_uuid  = "49535343-1E4D-4BD9-BA61-23c647249616"

evbee_write_pkt = None

def evbee_build_pkt(cmd, cmd_data):
    cmd_len = len(cmd_data)
    total_len = len(cmd_data) + 12
    rtn_ba = bytearray(b'\x5A\x5A') + total_len.to_bytes(2, 'little') + cmd.to_bytes(2, 'little') + cmd_len.to_bytes(2, 'little') + cmd_data
    rtn_ba = rtn_ba + binascii.crc32(rtn_ba).to_bytes(4, 'little')
    return rtn_ba

def evbee_decode_pkt(pkt_data):
    rtn_dict = {}
    if pkt_data[0] == 0x5A and pkt_data[1] == 0x5A:
        rtn_dict["cmd"] = int.from_bytes(pkt_data[4:6], 'little')
        rtn_dict["datalen"] = int.from_bytes(pkt_data[6:8], 'little')
        rtn_dict["data"] = pkt_data[8:(len(pkt_data)-4)]
    return rtn_dict

def evbee_handle_cmd(decoded):
    global evbee_write_pkt
    if(decoded["cmd"] == 0x0001):
        cmd_data = bytearray(b'\x01\x30\x00\x00')
        unix_ts = int(time.time())
        cmd_data = cmd_data + unix_ts.to_bytes(4, 'little')
        evbee_write_pkt = evbee_build_pkt(0x0004, cmd_data)

def notification_handler(characteristic: BleakGATTCharacteristic, data: bytearray):
    """Simple notification handler which prints the data received."""
    decoded = evbee_decode_pkt(data)
    print("Received notify from ", characteristic, decoded)
    evbee_handle_cmd(decoded)


async def main():
    global evbee_write_pkt
    print("Searching for BLE device '%s'", evbee_name)
    device = await BleakScanner.find_device_by_name(evbee_name)
    if device is None:
        print("Device '%s' not found", evbee_name)
        return

    print("Connecting to '%s'", evbee_name)

    async with BleakClient(device) as client:
        print("Connected")
        await client.start_notify(evbee_notify_uuid, notification_handler)
        pkt_init = evbee_build_pkt(0x0000, b'12345600')
        await client.write_gatt_char(evbee_write_uuid, pkt_init, response=True)
        while True:
            await asyncio.sleep(0.01)
            if evbee_write_pkt != None:
                await client.write_gatt_char(evbee_write_uuid, evbee_write_pkt, response=True)
                evbee_write_pkt = None

if __name__ == "__main__":
    asyncio.run(main())
