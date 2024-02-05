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
import datetime

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

    # 0x0001 = Response to 0x0000 initialise command. Send 0x0004 command to set the time
    if(decoded["cmd"] == 0x0001):
        cmd_data = bytearray(b'\x01\x30\x00\x00')
        unix_ts = int(time.time())
        cmd_data = cmd_data + unix_ts.to_bytes(4, 'little')
        evbee_write_pkt = evbee_build_pkt(0x0004, cmd_data)

    # 0x0005 = Response to 0x0004 set time command. Send 0x00A4 command to get faults. This response contains the firmware version etc.
    elif(decoded["cmd"] == 0x0005):
        evbee_write_pkt = evbee_build_pkt(0x00A4, b'\x01\x00\x00\x00')

    # 0x00A5 = Response to 0x00A4 get faults command. Send 0x00A6 to get currents. TODO: Figure out the format for the faults
    elif(decoded["cmd"] == 0x00A5):
        evbee_write_pkt = evbee_build_pkt(0x00A6, b'')

    # 0x00A7 = Response to 0x00A6 get current command. Could do something with setting the current, but for now do nothing
    elif(decoded["cmd"] == 0x00A7):
        print("Currents, min = ", int.from_bytes(decoded["data"][0:2], 'little'), ", max = ", int.from_bytes(decoded["data"][2:4], 'little'))

    # 0x0104 = Charger status update. Send 0x0105 to ACK the update and keep the time up to date
    elif(decoded["cmd"] == 0x0104):
        print("Staus update: Plug status = ", 
              int(decoded["data"][1]), 
              ", Voltage = ", 
              int.from_bytes(decoded["data"][4:6], 'little') / 100.0, 
              "V, Current = ", 
              int.from_bytes(decoded["data"][6:8], 'little') / 100.0,
              "A, Charge Time = ",
              int.from_bytes(decoded["data"][8:12], 'little'),
              "s, Energy = ",
              int.from_bytes(decoded["data"][12:14], 'little') / 1000.0,
              "kWh")
        unix_ts = int(time.time())
        evbee_write_pkt = evbee_build_pkt(0x0004, unix_ts.to_bytes(4, 'little'))
    
    # 0x0105 = Different charger status update. No response needed from this
    elif(decoded["cmd"] == 0x0105):
        print("Staus update2: Plug status = ", 
              int(decoded["data"][1]), 
              ", Voltage = ", 
              int.from_bytes(decoded["data"][4:6], 'little') / 100.0, 
              "V, Current = ", 
              int.from_bytes(decoded["data"][6:8], 'little') / 100.0,
              "A, Charge Time = ",
              int.from_bytes(decoded["data"][8:12], 'little'),
              "s, Energy = ",
              int.from_bytes(decoded["data"][12:14], 'little') / 1000.0,
              "kWh")
    


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
