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
import json

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from paho.mqtt import client as mqtt_client

#logger = logging.getLogger(__name__)

mqtt_broker = 'broker'
mqtt_port = 1883
mqtt_topic = "power/evse"
mqtt_cid = 'evbee'
mqtt_user = 'user'
mqtt_pass = 'pass'

# EVBee UUIDs
evbee_name         = "EVbee_6E0D"
evbee_service_uuid = "55535343-FE7D-4AE5-8FA9-9FAFD205E455"
evbee_write_uuid   = "48535343-1E4D-4BD9-BA61-23C647249616"
evbee_notify_uuid  = "49535343-1E4D-4BD9-BA61-23c647249616"

evbee_write_pkt = None
evbee_plug_status = 0
mq_client = None

# Check if charging allowed, based on Peak/OffPeak rates where I live
def is_charging_allowed():

    now = datetime.datetime.today()

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

def evbee_plug_status_str(status):
    if(status == 0):
        return 'unplugged'
    elif(status == 1):
        return 'waiting'
    elif(status == 2):
        return 'charging'
    else:
        return 'unknown'
    
# Reconnect code from https://www.emqx.com/en/blog/how-to-use-mqtt-in-python
FIRST_RECONNECT_DELAY = 1
RECONNECT_RATE = 2
MAX_RECONNECT_COUNT = 12
MAX_RECONNECT_DELAY = 60

def on_mqtt_disconnect(client, userdata, rc):
    logging.info("MQTT disconnected with result code: %s", rc)
    reconnect_count, reconnect_delay = 0, FIRST_RECONNECT_DELAY
    while reconnect_count < MAX_RECONNECT_COUNT:
        logging.info("MQTT reconnecting in %d seconds...", reconnect_delay)
        time.sleep(reconnect_delay)

        try:
            client.reconnect()
            logging.info("MQTT reconnected successfully!")
            return
        except Exception as err:
            logging.error("MQTT %s. reconnect failed. Retrying...", err)

        reconnect_delay *= RECONNECT_RATE
        reconnect_delay = min(reconnect_delay, MAX_RECONNECT_DELAY)
        reconnect_count += 1
    logging.info("MQTT reconnect failed after %s attempts. Exiting...", reconnect_count)

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
    global evbee_plug_status
    global mq_client

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
        status_update = {
            "plug": evbee_plug_status_str(evbee_plug_status),
            "voltage": int.from_bytes(decoded["data"][4:6], 'little') / 100.0,
            "current": int.from_bytes(decoded["data"][6:8], 'little') / 100.0,
            "timeoncharge": int.from_bytes(decoded["data"][8:12], 'little'),
            "energy": int.from_bytes(decoded["data"][12:14], 'little') / 1000.0
        }
        
        print("Status update: Plug status = ", 
              status_update["plug"], 
              ", Voltage = ", 
              status_update["voltage"], 
              "V, Current = ", 
              status_update["current"],
              "A, Charge Time = ",
              status_update["timeoncharge"],
              "s, Energy = ",
              status_update["energy"],
              "kWh")
        if(mq_client.is_connected()):
            mq_client.publish(mqtt_topic + '/status', json.dumps(status_update))
        unix_ts = int(time.time())
        evbee_write_pkt = evbee_build_pkt(0x0004, unix_ts.to_bytes(4, 'little'))
    
    # 0x0105 = Different charger status update. No response needed from this
    elif(decoded["cmd"] == 0x0105):
        evbee_plug_status = int(decoded["data"][1])
        status_update = {
            "plug": evbee_plug_status_str(evbee_plug_status),
            "voltage": int.from_bytes(decoded["data"][4:6], 'little') / 100.0,
            "current": int.from_bytes(decoded["data"][6:8], 'little') / 100.0,
            "timeoncharge": int.from_bytes(decoded["data"][8:12], 'little'),
            "energy": int.from_bytes(decoded["data"][12:14], 'little') / 1000.0
        }

        print("Status update2: Plug status = ", 
              status_update["plug"], 
              ", Voltage = ", 
              status_update["voltage"], 
              "V, Current = ", 
              status_update["current"],
              "A, Charge Time = ",
              status_update["timeoncharge"],
              "s, Energy = ",
              status_update["energy"],
              "kWh")
        if(mq_client.is_connected()):
            mq_client.publish(mqtt_topic + '/status', json.dumps(status_update))


def notification_handler(characteristic: BleakGATTCharacteristic, data: bytearray):
    """Simple notification handler which prints the data received."""
    decoded = evbee_decode_pkt(data)
    print("Received notify from ", characteristic, decoded)
    evbee_handle_cmd(decoded)


async def main():
    global evbee_write_pkt
    global mq_client
    evbee_charge_command_sent = int(time.time())

    
    mq_client = mqtt_client.Client(mqtt_cid)
    mq_client.username_pw_set(mqtt_user, mqtt_pass)
    mq_client.on_disconnect = on_mqtt_disconnect
    mq_client.connect(mqtt_broker, mqtt_port)
    mq_client.loop_start()

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
