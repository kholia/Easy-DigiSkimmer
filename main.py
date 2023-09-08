#!/usr/bin/env python3

import os
import re
import time
import threading
import subprocess
from datetime import datetime

from pskreporter import PskReporter

import serial

# https://pythex.org/ - use this for regex debugging
# https://en.wikipedia.org/wiki/Maidenhead_Locator_System

state = {}
state["frequency"] = 14.075  # TODO


def process_msg(msg):
    callsign = None
    # CQ CALL LLnn
    m = re.match(r'^CQ\s([\w\d\/]{3,})\s(\w\w\d\d)', msg)
    if m:
        callsign = m.group(1)
        grid = m.group(2)
        return callsign, grid
    # CQ CALL LLn      a1
    m = re.match(r'^CQ\s([\w\d\/]{3,})\s(\w\w\d\d)[\s]+a', msg)
    if m:
        callsign = m.group(1)
        grid = m.group(2)
        return callsign, grid
    # CQ [NA,DX,xx] CALL LLnn | CQ ASIA VU3CER MK68
    m = re.match(r'^CQ\s\w{2,4}\s([\w\d\/]{3,})\s(\w\w\d\d)', msg)
    if m:
        callsign = m.group(1)
        grid = m.group(2)
        return callsign, grid
    # CQ ASIA PY7ZZ
    m = re.match(r'^CQ\s\w{2,4}\s([\w\d\/]{3,})', msg)
    if m:
        callsign = m.group(1)
        grid = ""
        return callsign, grid
    # CALL1 CALL2 [R][-+]nn
    m = re.match(r'^[\w\d\/]{3,}\s([\w\d\/]{3,})\sR*[\-+][0-9]{2}', msg)
    if m:
        callsign = m.group(1)
        grid = ""
        return callsign, grid
    # CALL1 CALL2 RRR
    m = re.match(r'^[\w\d\/]{3,}\s([\w\d\/]{3,})\sRRR', msg)
    if m:
        callsign = m.group(1)
        grid = ""
        return callsign, grid
    # CALL1 CALL2 RR73 or 73
    m = re.match(r'[\w\d\/]{3,}\s([\w\d\/]{3,})\sR*73', msg)
    if m:
        callsign = m.group(1)
        grid = ""
        return callsign, grid
    # CALL1 CALL2 GRID
    m = re.match(r'[\w\d\/]{3,}\s([\w\d\/]{3,})\s(\w\w\d\d)', msg)
    if m:
        callsign = m.group(1)
        grid = m.group(2)
        return callsign, grid

    # Handle -> 000000 -18  0.1 1865 ~  <...> IV3VBM -07
    if not callsign:
        cols = msg.split()
        callsign = cols[1]  # hackish!
        grid = ""

    return callsign, grid


def test_process_msg_1():
    callsign, grid = process_msg("CQ VU3CER MK68")
    assert callsign == "VU3CER"

def test_process_msg_2():
    callsign, grid = process_msg("CQ ASIA PY7ZZ")
    assert callsign == "PY7ZZ"

def test_process_msg_3():
    callsign, grid = process_msg("VU3CER VU3FOE R-01")
    assert callsign == "VU3FOE"

def test_process_msg_4():
    callsign, grid = process_msg("CQ R2DA KO95")
    assert callsign == "R2DA"


def parser(lines):
    """
    000000   0 -0.7 1151 ~  VU3CER VU3FOE MK68
    <DecodeFinished>   0   1
    """

    count = 0

    for line in lines.splitlines():
        if "~" in line:
            count = count + 1
            print(line)
            d = line.split()
            _, db, dt, offset, _, *msg = d
            msg = " ".join(msg)
            print(db, dt, offset, msg)
            callsign, grid = process_msg(msg)
            out = {}
            out["mode"] = "FT8"
            out["timestamp"] = int(time.time() - 15)  # The "- 15" part is a hack
            out["db"] = db
            out["dt"] = dt
            out["freq"] = state["frequency"]
            out["msg"] = msg
            out["callsign"] = callsign
            out["locator"] = grid
            PskReporter.getSharedInstance("VU3CER").spot(out)  # receiver's callsign

    return count


def action(marker=0):
    filename = "/tmp/%s.wav" % marker
    # blocking audio recording call
    result = subprocess.run("cd /tmp; arecord -c 1 -t wav -f S16_LE -r 12000 -d 15 %s" % filename, capture_output=True, text=True, shell=True)
    # blocking ft8 decode
    cmd = "cd /tmp/; jt9 --ft8 %s" % filename
    result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    output = result.stdout
    print(output)
    count = parser(output)

    # dirty hack
    if count > 0:
        result = subprocess.run('echo "%s" > /tmp/extend.txt' % count, shell=True)
    else:
        result = subprocess.run('rm -f /tmp/extend.txt', shell=True)


def switch_band(band_number):
    ser = serial.Serial('/dev/ttyACM0')
    ser.write(('%s' % band_number).encode())
    if band_number == 0:
        state["frequency"] = 7.074
    elif band_number == 1:
        state["frequency"] = 10.136
    elif band_number == 2:
        state["frequency"] = 14.074
    elif band_number == 3:
        state["frequency"] = 18.100
    elif band_number == 4:
        state["frequency"] = 21.074
    elif band_number == 5:
        state["frequency"] = 24.915
    elif band_number == 6:
        state["frequency"] = 28.074
    line = ser.readline()
    ser.close()

if __name__ == "__main__":
    count = 0
    band_number = 0
    upper_count = 6

    switch_band(4)  # 15m is hot

    while True:
        # get time
        t = time.localtime(time.time())
        if t.tm_sec % 15 == 0:
            count = count + 1
            x = threading.Thread(target=action, args=(t.tm_sec,))
            # action(marker=t.tm_sec)
            x.start()
            time.sleep(2)  # hack to avoid re-looping for the same second in time!
            if os.path.exists("/tmp/extend.txt"):
                upper_count = upper_count + 1
                print("Extending upper_count to (%s), current count is (%s)" % (upper_count, count))
            if count == upper_count:
                # switch band
                band_number = (band_number + 1) % 7
                print("Switching bands to (%d)" % band_number)
                switch_band(band_number)
                count = 0
                upper_count = 6
        time.sleep(0.1)
