import time

from pskreporter import PskReporter

from datetime import datetime

keys = ["callsign", "timestamp",
                "locator", "db", "freq", "mode", "msg"]

out = {}
out["mode"] = "FT8"
out["timestamp"] = int(time.time() - 15)
out["db"] = "-15"
out["dt"] = "0"
out["freq"] = 14.075
out["msg"] = "CQ VU3CER MK68"
out["callsign"] = "WQ6W"
out["locator"] = "CM87"

PskReporter.getSharedInstance("VU3FOE").spot(out)

while True:
    time.sleep(1)
