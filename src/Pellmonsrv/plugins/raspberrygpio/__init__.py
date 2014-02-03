#! /usr/bin/python
# -*- coding: utf-8 -*-
"""
    Copyright (C) 2013  Anders Nylund

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

from Pellmonsrv.plugin_categories import protocols
from multiprocessing import Process, Queue
from threading import Thread, Timer
import RPi.GPIO as GPIO
from time import time, sleep
from ConfigParser import ConfigParser
from os import path
import os, grp, pwd
import mmap
import signal
import sys
from threading import Event

itemList=[]
itemTags = {}
Menutags = ['raspberryGPIO']


def signal_handler(signal, frame):
    """ GPIO needs cleaning up on exit """
    GPIO.cleanup()
    sys.exit(0)

class root(Process):
    """GPIO needs root, so we fork off this process before the server drops privileges"""
    def __init__(self, request, response, itemList):
        super (root, self).__init__()
        self.last_time = 0
        self.buf = [0]*500
        self.index = 0
        self.f = 0
        self.request = request
        self.response = response
        self.itemList = itemList
        self.ev = Event()
        self.last_edge = 0
        self.count = []

    def edge_callback(self, channel):
        """Called by RpiGPIO interrupt handle on """
        self.last_edge = time()
        self.ev.set()

    def filter_thread(self):
        """Handle debounce filtering of the inputs"""
        while True:
            self.ev.wait()
            if time() - last_edge > 0.1:
                self.ev.clear()
                currentstate = GPIO.input(26)
                if currentstate == 0:
                    self.count[0] += 1
            else:
                 sleep(0.05)

    def timer(self):
        """Read the 64bit freerunning megaherz timer of the broadcom chip"""
        self.m.seek(4)
        s =  self.m.read(8)
        o = 0
        for c in range(0,7):
            o += ord(s[c])<<(8*c)
        return o

    def tacho_callback(self, channel):
        """Called by falling edge interrupt on the tachometer input to calculate rpm"""
        time = self.timer()
        timediff = time - self.last_time
        self.last_time = time
        self.buf[self.index] = timediff
        if self.index == 499:
            self.index = 0
        else:
            self.index += 1

    def run(self):
        GPIO.setmode(GPIO.BOARD)
        signal.signal(signal.SIGINT, signal_handler)

        for item in itemList:
            if item['function'] == 'counter':
                pin = item['pin']
                self.count = [0]
                GPIO.setup(pin, GPIO.IN, pull_up_down = GPIO.PUD_UP)
                GPIO.add_event_detect(pin, GPIO.FALLING, callback = self.edge_callback)
                t = Thread(target=self.filter_thread)
                t.setDaemon(True)
                t.start()
                
            elif item['function'] == 'tachometer':
                pin = item['pin']
                mem = open ('/dev/mem','r')
                self.m = mmap.mmap(mem.fileno(), 4096, mmap.MAP_SHARED, mmap.PROT_READ, offset=0x20003000)
                GPIO.setup(pin, GPIO.IN)
                self.last_time= self.timer()
                GPIO.add_event_detect(pin, GPIO.FALLING, callback = self.tacho_callback)

            elif item['function'] == 'input':
                pass
            elif item['function'] == 'output':
                pass
                
        """Wait for a request command"""
        x = self.request.get()
        while not x=='quit':
            if x == 'tachometer':
                buf = list(self.buf)
                index = self.index
                i = index
                lapse = 0
                while i>0 and lapse < 10500000:
                    lapse += buf[i]
                    i-= 1
                b = buf[i:index] 
                i = 499
                if lapse < 10500000:
                    while i>index and lapse < 10500000:
                        lapse += buf[i]
                        i-=1
                    b += buf[i:500]
                b.sort()
                l = len(b)-1
                if l>30:
                    s = b[20]
                else:
                    s = b[0]
                if s>1:
                    self.f = 1/float(s) * 1000000 * 60
                else:
                    self.f=0
                self.response.put(int(self.f))
            elif x == 'counter':
                self.response.put(int(self.count[0]))
            else:
                try:
                    if x[0] == 'counter':
                        self.count[0] = int(x[1])
                        self.response.put('OK')
                    else:
                        self.response.put('what?')
                except:
                    response.put('error')
            x=self.request.get()

class raspberry_gpio(protocols):
    def __init__(self):
        protocols.__init__(self)
        self.power = 0

    def activate(self, conf, glob):
        protocols.activate(self, conf, glob)
        self.pin2index={}
        self.name2index={}
        for key,value in self.conf.iteritems():
            try:
                pin_name = key.split('_')[0]
                pin_data = key.split('_')[1]
                if not self.pin2index.has_key(pin_name):
                    itemList.append({'min':'', 'max':'', 'unit':'', 'type':'R', 'description':''})
                    self.pin2index[pin_name] = len(itemList)-1
                if pin_data == 'function':
                    itemList[self.pin2index[pin_name]]['function'] = value
                    if value == 'counter':
                        itemList[self.pin2index[pin_name]]['type'] = 'R/W'
                elif pin_data == 'item':
                    itemList[self.pin2index[pin_name]]['name'] = value
                    itemTags[value] = ['All', 'raspberryGPIO', 'Basic']
                    self.name2index[value]=len(itemList)-1
                elif pin_data == 'pin':
                    itemList[self.pin2index[pin_name]]['pin'] = int(value)
            except Exception,e:
                logger.info(str(e))
        signal.signal(signal.SIGINT, signal_handler)
        self.request = Queue()
        self.response = Queue()
        self.root = root(self.request, self.response, itemList)
        self.root.start()

    def deactivate(self):
        protocols.deactivate(self)
        self.request.put('quit')
        self.root.join()
        GPIO.cleanup()

    def getItem(self, item):
        if self.name2index.has_key(item):
            function =itemList[self.name2index[item]]['function']
            if function in['counter', 'tachometer']:
                self.request.put(function)
                try:
                    return str(self.response.get(0.2))
                except:
                    return str('timeout') 
            else:
                return 'error'
        else:
            return 'error'

    def setItem(self, item, value):
        if self.name2index.has_key(item):
            if itemList[self.name2index[item]]['function'] == 'counter':
                self.request.put(('counter', value))
                try:
                    r = self.response.get(5)
                except:
                    r='timeout'
                return str(r)
        else:
            return['error']

    def getDataBase(self):
        l=[]
        for item in itemList:
            l.append(item['name'])
        return l

    def GetFullDB(self, tags):
        def match(requiredtags, existingtags):
            for rt in requiredtags:
                if rt != '' and not rt in existingtags:
                    return False
            return True
        items = [item for item in itemList if match(tags, itemTags[item['name']]) ]
        for item in items:
            item['description'] = ''
        return items
        
    def getMenutags(self):
        return Menutags


