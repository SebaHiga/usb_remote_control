from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode
from adafruit_httpserver import Server, Request, Response, FileResponse
import adafruit_logging as log
import time
import re
import usb_hid
import board
import digitalio
import asyncio
import pwmio
import time
import wifi
import socketpool

DEBUG_LOCAL = True


SESSION_MAX_TIME = 60 * 60 * 18
#SESSION_MAX_TIME = 60
SESSION_TIME_SLEEP = 0.5
SESSION_MAX_TIME_PRESSED = 5

VT_PIN = board.GP0
BT_A_PIN = board.GP5
BT_B_PIN = board.GP4
BT_C_PIN = board.GP3
BT_D_PIN = board.GP2
BUZZER_PIN = board.GP11

class Context:
    def __init__(self):
        self.session_time = time.time()
        self.session_active = False

class HandlerBase:
    def __init__(self):
        pass

    def on_notify(self, button_status):
        pass

ANNOUNCE_INIT = 0
ANNOUNCE_ARM = 1
ANNOUNCE_DISARM = 2
ANNOUNCE_TEST = 3
BUZZER_DC_ON = 2**15 
BUZZER_DC_OFF = 0

class Logger:
    def __init__(self):
        self.content = []

    def print(self, content):
        print(content)
        self.content.append(content)

        if len(self.content) > 50:
            self.content.pop(0)

    def get_logs(self):
        ret = ""

        for c in reversed(self.content):
            ret += c + '<br>'

        print(ret)
        return ret

log = Logger()

class Buzzer:
    def __init__(self):
        self.buzzer = pwmio.PWMOut(BUZZER_PIN, variable_frequency=True)
        self.buzzer.frequency = 1000
        self.buzzer.duty_cycle = BUZZER_DC_OFF

        self.notes = {
            'C': 32,
            'C#': 34,
            'D': 36,
            'D#': 38,
            'E': 41,
            'F': 43,
            'F#': 46,
            'G': 49,
            'G#': 52,
            'A': 55,
            'A#': 58,
            'B': 61
        }
    
    def play(self, note = 0, octave = 0, lenght = 0):
        self.buzzer.duty_cycle = BUZZER_DC_ON
        self.buzzer.frequency = self.notes[note] * (2 ** octave)
        time.sleep(lenght)
        self.buzzer.duty_cycle = BUZZER_DC_OFF
        
    def wait(self, lenght):
        time.sleep(lenght)

class Notificator:
    def __init__(self):
        self.buzzer = Buzzer()

    def announce(self, type):
        if type is ANNOUNCE_INIT:
            self.buzzer.play('G', 3, 0.05)
            self.buzzer.play('G', 4, 0.05)
        if type is ANNOUNCE_ARM:
            self.buzzer.play('B', 4, 0.1)
            self.buzzer.wait(0.1)
            self.buzzer.play('B', 4, 0.1)
            self.buzzer.wait(0.1)
            self.buzzer.play('B', 4, 0.1)
        if type is ANNOUNCE_DISARM:
            self.buzzer.play('F', 3, 0.5)
            self.buzzer.wait(0.1)
            self.buzzer.play('F', 3, 0.5)            
            
class SessionHandler(HandlerBase):
    def __init__(self, context, notificator):
        self.context = context
        self.combination_pressed = False
        self.notificator = notificator
        self.notificator.announce(ANNOUNCE_INIT)


    def reset_session(self):
        self.context.session_time = time.time()

    def on_notify(self, button_status):
        vt, button_a, button_b, button_c, button_d = button_status

        self.combination_pressed = button_a is True and button_d is True

    def run(self):
        delta = time.time() - self.context.session_time

        if self.context.session_active is True:

            if delta > SESSION_MAX_TIME:
                log.print(f'Session expired')
                self.notificator.announce(ANNOUNCE_DISARM)
                self.context.session_active = False

            if self.combination_pressed:
                time.sleep(SESSION_MAX_TIME_PRESSED)
                if self.combination_pressed:
                    log.print("Force exit session")
                    self.notificator.announce(ANNOUNCE_DISARM)
                    self.context.session_active = False
        else:
            if self.combination_pressed:
                log.print("Activating session")
                self.notificator.announce(ANNOUNCE_ARM)
                self.context.session_time = time.time()
                self.context.session_active = True
    
class ControlHandler(HandlerBase):
    def __init__(self, context):
        self.context = context
        self.keyboard = Keyboard(usb_hid.devices)

        self.STATE_IDLE = 0
        self.STATE_PRESS = 1
        self.STATE_PRESSED = 2

        self.state = self.STATE_IDLE


    def performSingleKeyStroke(self, keycode):
        self.keyboard.press(keycode)
        self.keyboard.release(keycode)

    def on_notify(self, button_status):
        vt, button_a, button_b, button_c, button_d = button_status

        if self.context.session_active is False:
            return
        
        if self.state == self.STATE_IDLE:
            if vt is False:
                return

            if not (button_a ^ button_b ^ button_c ^ button_d):
                return

            if button_a:
                self.performSingleKeyStroke(Keycode.SPACEBAR)
                log.print("Pressing A")
            elif button_b:
                self.performSingleKeyStroke(Keycode.RIGHT_ARROW)
                log.print("Pressing B")
            elif button_c:
                self.performSingleKeyStroke(Keycode.LEFT_ARROW)
                log.print("Pressing C")
            elif button_d:
                self.performSingleKeyStroke(Keycode.P)
                log.print("Pressing D")

            self.state = self.STATE_PRESSED
    
        elif self.state == self.STATE_PRESSED:
            if vt is not True:
                self.state = self.STATE_IDLE  

class ButtonHandler:
    def __init__(self, context):
        self.context = context
        self.vt = digitalio.DigitalInOut(VT_PIN)
        self.vt.direction = digitalio.Direction.INPUT
        self.vt.pull = digitalio.Pull.DOWN

        self.button_d = digitalio.DigitalInOut(BT_A_PIN)
        self.button_d.direction = digitalio.Direction.INPUT
        self.button_d.pull = digitalio.Pull.DOWN

        self.button_c = digitalio.DigitalInOut(BT_B_PIN)
        self.button_c.direction = digitalio.Direction.INPUT
        self.button_c.pull = digitalio.Pull.DOWN

        self.button_b = digitalio.DigitalInOut(BT_C_PIN)
        self.button_b.direction = digitalio.Direction.INPUT
        self.button_b.pull = digitalio.Pull.DOWN

        self.button_a = digitalio.DigitalInOut(BT_D_PIN)
        self.button_a.direction = digitalio.Direction.INPUT
        self.button_a.pull = digitalio.Pull.DOWN

        self.observers = []
        
    def run(self):
        self.notify_observers()

    def get_keystrokes(self):
        return (self.vt.value, self.button_a.value, self.button_b.value, self.button_c.value, self.button_d.value)

    def subscribe_observer(self, observer):
        self.observers.append(observer)

    def notify_observers(self):
        for observer in self.observers:
            observer.on_notify(self.get_keystrokes())
          

class ServerHandler:
    def __init__(self, context):
        self.observers = []
        self.context = context

        try:
            if DEBUG_LOCAL:
                wifi.radio.connect("ATR", "igualnohayinternet")
                
            else:
                if not wifi.radio.ap_active:
                    wifi.radio.start_ap("apabcdef", "rpipico1234")

            self.pool = socketpool.SocketPool(wifi.radio)
            self.server = Server(self.pool, "/static", debug=True)
        except Exception as e:
            log.print(f'Could get AP or WiFi module ready: {e}')

        address = wifi.radio.ipv4_address_ap if DEBUG_LOCAL is False else wifi.radio.ipv4_address
        print(address)
        self.server.start(str(address))

        @self.server.route("/")
        def base(request: Request):
            return FileResponse(request, filename="index.html", root_path="www")

        @self.server.route("/start")
        def start(request: Request):
            self.notify_observers((True, True, False, False, False))

            return Response(request, "ok")

        @self.server.route("/unlock")
        def start(request: Request):
            self.session_time = time.time()
            self.session_active = True
            self.notify_observers((True, True, False, False, True))

            return Response(request, "ok")

        @self.server.route("/logs")
        def start(request: Request):
            return Response(request, log.get_logs())

    def run(self):
        self.server.poll()

    def subscribe_observer(self, observer):
        self.observers.append(observer)

    def notify_observers(self, key_combinations):
        for observer in self.observers:
            observer.on_notify(key_combinations)
    
def main(): 
    context = Context()
    notificator = Notificator()
    button_handler = ButtonHandler(context)
    session_handler = SessionHandler(context, notificator)
    control_handler = ControlHandler(context)
    server_handler = ServerHandler(context)

    button_handler.subscribe_observer(session_handler)
    button_handler.subscribe_observer(control_handler)

    server_handler.subscribe_observer(session_handler)
    server_handler.subscribe_observer(control_handler)

    while True:
        try:
            session_handler.run()
            button_handler.run()
            server_handler.run()
        except Exception as e:
            log.print(f"Exception: {e}")

main()
