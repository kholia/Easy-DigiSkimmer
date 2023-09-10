import os
import logging
import threading
from threading import Thread
import time
import random
from functools import reduce
from operator import and_
import digiskr.config as config
from digiskr.config import Config

import requests


class Wsprnet(object):
    sharedInstance = {}
    creationLock = threading.Lock()
    # avoid the two minute boundaries
    interval = 45
    supportedModes = ["WSPR", "FST4W"]

    @staticmethod
    def getSharedInstance(station: str):
        with Wsprnet.creationLock:
            if Wsprnet.sharedInstance.get(station) is None:
                Wsprnet.sharedInstance[station] = Wsprnet(station)
        return Wsprnet.sharedInstance[station]

    @staticmethod
    def stop():
        [psk.cancelTimer() for psk in Wsprnet.sharedInstance.values()]

    def __init__(self, station: str):
        self.spots = []
        self.spotLock = threading.Lock()
        self.station = station
        self.timer = None

        # prepare tmpdir for uploader
        self.tmpdir = os.path.join(
            Config.tmpdir(), station, "WSPR", "wsprnet.uploader")
        self.logdir = os.path.join(
            Config.logdir(), "spots", "wsprnet", station)
        os.makedirs(self.tmpdir, exist_ok=True)
        os.makedirs(self.logdir, exist_ok=True)

        self.uploader = Uploader(station, self.tmpdir, self.logdir)

    def scheduleNextUpload(self):
        if self.timer:
            return
        delay = Wsprnet.interval + random.uniform(0, 15)
        logging.info("scheduling next wsprnet upload in %3.2f seconds", delay)
        self.timer = threading.Timer(delay, self.upload)
        self.timer.setName("wsprnet.uploader-%s" % self.station)
        self.timer.start()

    def spotEquals(self, s1, s2):
        keys = ["callsign", "timestamp",
                "locator", "db", "freq", "mode", "msg"]

        return reduce(and_, map(lambda key: s1[key] == s2[key], keys))

    def spot(self, spot):
        if not spot["mode"] in Wsprnet.supportedModes:
            return
        with self.spotLock:
            if any(x for x in self.spots if self.spotEquals(spot, x)):
                # dupe
                pass
            else:
                self.spots.append(spot)
        self.scheduleNextUpload()

    def upload(self):
        try:
            with self.spotLock:
                spots = self.spots
                self.spots = []
            if spots:
                self.uploader.upload(spots)
                self.timer = None
        except Exception:
            logging.exception("Failed to upload spots")

    def cancelTimer(self):
        if self.timer:
            self.timer.cancel()
            self.timer.join()
        self.timer = None


class Uploader(object):

    def __init__(self, station: str, tmpdir, logdir):
        self.station = Config.get()["STATIONS"][station]
        self.station["name"] = station
        self.tmpdir = tmpdir
        self.logdir = logdir
        self.event = threading.Event()

    def upload(self, spots):
        logging.warning("uploading %i spots to wsprnet", len(spots))

        allmet = os.path.join(self.tmpdir, "allmet_%d.txt" % (
            int(time.time() + random.uniform(0, 99)) & 0xffff))

        conf = Config.get()
        spot_lines = []
        for spot in spots:
            # The 11th field is identification of WSPR and FST4W (http://wsprnet.org/drupal/node/8500)            #
            # the `mode` according to 4 values:
            # "2"
            # "15" for the current WSPR modes -2 & -15
            # "5
            # "30" for the FST4W modes 300 & 1800
            #
            # example:
            # 200804 1916  0.26 -18  0.96   7.040176 JA5NVN         PM74   37  0  15

            mode = 2 if spot["mode"] == "WSPR" else conf["WSJTX"]["interval"]["FST4W"] / 60 \
                if "WSJTX" in conf and "interval" in conf["WSJTX"] and "FST4W" in conf["WSJTX"]["interval"] else 2
            spot_lines.append("%s  %1.2f %d  %1.2f   %2.6f %s         %s   %d  %d  %d\n" % (
                # wsprnet needs GMT time
                time.strftime("%y%m%d %H%M", time.gmtime(spot["timestamp"])),
                spot["sync_quality"],
                spot["db"],
                spot["dt"],
                # freq in MHz for wsprnet
                spot["freq"],
                spot["callsign"],
                spot["locator"],
                spot["watt"],
                spot["drift"],
                mode
            ))

        self.save(spot_lines, allmet)
        self.saveall(spot_lines)

        postfiles = {"allmept": open(allmet, "r")}
        params = {"call": self.station["callsign"],
                  "version": "DS_" + config.VERSION,
                  "grid": self.station["grid"]}

        max_retries = 3
        retries = 0
        resp = None

        while True:
            try:
                requests.adapters.DEFAULT_RETRIES = 5
                s = requests.session()
                s.keep_alive = False
                resp = s.post("http://wsprnet.org/post",
                              files=postfiles, params=params, timeout=300)

                if resp.status_code == 200:
                    # if we can not find the text of success
                    if resp.text.find("spot(s) added") == -1:
                        self.savefail(spot_lines)
                    break

            # TODO: handle with retry
            except requests.ConnectionError or requests.exceptions.Timeout as e:
                logging.error("Wsprnet connection error %s", e)
                if retries >= max_retries:
                    logging.warning(
                        "Saving %d spot to wspr_upload_fail.log", len(spot_lines))
                    self.savefail(spot_lines)
                    break
                else:
                    retries += 1
                    logging.warning("wait 10s to try again...->%d", retries)
                    self.event.wait(timeout=10)
                    continue
            except requests.exceptions.ReadTimeout as e:
                logging.error("Wsprnet read timeout error %s", e)
                self.savefail(spot_lines)
                break

        logging.debug("delete %s", allmet)
        os.unlink(allmet)

    def saveall(self, spot_lines):
        self.savelog(spot_lines, "")

    def savefail(self, spot_lines):
        self.savelog(spot_lines, "_FAIL")

    # FIXME: add MAX_LOG_FILES to avoid disk full
    def savelog(self, spot_lines, type):
        if "LOG_SPOTS" in Config.get() and Config.get()["LOG_SPOTS"]:
            file = os.path.join(self.logdir, "%s%s.log" % (
                time.strftime("%y%m%d", time.localtime()), type))
            self.save(spot_lines, file)

    def save(self, spot_lines, file):
        with open(file, "a") as f:
            f.writelines(spot_lines)
