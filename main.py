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
evbee_plug_status = 0

# Check if charging allowed, based on Peak/OffPeak rates where I live
def is_charging_allowed():

    now = datetime.datetime.today()
    
    # Temp allow charging now
    #return True

    # Allow charging 24/7 on the weekend
    if now.weekday() >= 5:
        return True
    
    # Allow charging between 00:00 - 07:00 on weekday
    if now.hour < 7:
        return True
    
    # Allow charging between 11:00 - 17:00 on weekday
    if now.hour >= 11 and now.hour < 17:
        return True
    
    # Allow charging after 21:00 on weekday
    if now.hour >= 21:
        return True
    
    # Deny charging 07:00 - 11:00 and 17:00 - 21:00 on weekday
    return False

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
        pkt_data_end = min(len(pkt_data) - 4, rtn_dict["datalen"] + 8)
        rtn_dict["data"] = pkt_data[8:pkt_data_end]
    return rtn_dict

def evbee_handle_cmd(decoded):
    global evbee_write_pkt
    global evbee_plug_status

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
        evbee_plug_status = int(decoded["data"][1])
        print("Staus update: Plug status = ", 
              evbee_plug_status, 
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
        evbee_plug_status = int(decoded["data"][1])
        print("Staus update2: Plug status = ", 
              evbee_plug_status, 
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
    if decoded["datalen"] < (len(data) - 12):    # More data to decode
        decoded2 = evbee_decode_pkt(data[decoded["datalen"] + 12])
        evbee_handle_cmd(decoded2)


async def main():
    global evbee_write_pkt
    evbee_charge_command_sent = int(time.time())

    while True:
        print("Searching for BLE device", evbee_name)
        device = await BleakScanner.find_device_by_name(evbee_name)
        if device is None:
            print("Device not found. Will try again later")
            await asyncio.sleep(30)
            continue

        print("Connecting to", evbee_name)

        async with BleakClient(device) as client:
            print("Connected")
            evbee_write_pkt = None
            await client.start_notify(evbee_notify_uuid, notification_handler)
            pkt_init = evbee_build_pkt(0x0000, b'12345600')
            await client.write_gatt_char(evbee_write_uuid, pkt_init, response=True)
            while client.is_connected:
                await asyncio.sleep(0.01)
                unix_ts = int(time.time())

                if evbee_write_pkt != None:
                    await client.write_gatt_char(evbee_write_uuid, evbee_write_pkt, response=True)
                    evbee_write_pkt = None
                
                # Only send max 1 charge/discharge command every 30 seconds
                if unix_ts - evbee_charge_command_sent > 30:
                    # Car plugged in, not charging and charging allowed, send start charge command
                    if evbee_plug_status == 1 and is_charging_allowed():
                        cmddata = b'\x00\x00\x00\x00\x00\x00\x00\x00' + unix_ts.to_bytes(4, 'little') + b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
                        pkt = evbee_build_pkt(0x0100, cmddata)
                        print("Sending start charge command")
                        await client.write_gatt_char(evbee_write_uuid, pkt, response=True)
                        evbee_charge_command_sent = int(time.time())

                    # Car plugged is charging and charging not, send stop charge command
                    elif evbee_plug_status == 2 and not is_charging_allowed():
                        cmddata = b'\x00\x00\x00\x00'
                        pkt = evbee_build_pkt(0x0102, cmddata)
                        print("Sending stop charge command")
                        await client.write_gatt_char(evbee_write_uuid, pkt, response=True)
                        evbee_charge_command_sent = int(time.time())
            

if __name__ == "__main__":
    asyncio.run(main())
