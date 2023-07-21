import time
import re
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode
import adafruit_logging as log
import board
import digitalio
import asyncio
import pwmio
import time

import wifi
import socketpool

SESSION_MAX_TIME = 60 * 60 * 24
#SESSION_MAX_TIME = 60
SESSION_TIME_SLEEP = 0.5
SESSION_MAX_TIME_PRESSED = 5

VT_PIN = board.GP0
BT_A_PIN = board.GP5
BT_B_PIN = board.GP4
BT_C_PIN = board.GP3
BT_D_PIN = board.GP2
BUZZER_PIN = board.GP11


try:
    addr = socketpool.SocketPool(wifi.radio).getaddrinfo('0.0.0.0', 80)[0][-1]
    wifi.radio.enabled = True
    wifi.radio.start_ap("apabcdef", "rpipico1234")
    server_socket = socketpool.SocketPool(wifi.radio).socket()
    server_socket.bind(addr)
    server_socket.listen(1)

except Exception as e:
    import microcontroller
    print(f'exception {e}')

class Context:
    def __init__(self):
        self.session_time = time.time()
        self.session_active = False

class HandlerBase:
    def __init__(self):
        pass

    async def on_notify(self, button_status):
        pass

ANNOUNCE_INIT = 0
ANNOUNCE_ARM = 1
ANNOUNCE_DISARM = 2
ANNOUNCE_TEST = 3
BUZZER_DC_ON = 2**15 
BUZZER_DC_OFF = 0

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
        
    async def play(self, note = 0, octave = 0, time = 0):
        self.buzzer.duty_cycle = BUZZER_DC_ON
        self.buzzer.frequency = self.notes[note] * (2 ** octave)
        await asyncio.sleep(time)
        self.buzzer.duty_cycle = BUZZER_DC_OFF
        
    async def wait(self, time):
        await asyncio.sleep(time)

class Notificator:
    def __init__(self):
        self.buzzer = Buzzer()

    async def announce(self, type):
        if type is ANNOUNCE_INIT:
            await self.buzzer.play('G', 3, 0.05)
            await self.buzzer.play('G', 4, 0.05)
        if type is ANNOUNCE_ARM:
            await self.buzzer.play('G', 3, 0.1)
            await self.buzzer.wait(0.1)
            await self.buzzer.play('G', 3, 0.1)
            await self.buzzer.play('G', 4, 0.1)
        if type is ANNOUNCE_DISARM:
            await self.buzzer.play('F', 3, 0.5)
            await self.buzzer.wait(0.1)
            await self.buzzer.play('F', 3, 0.5)            
        if type is ANNOUNCE_TEST:
            await self.buzzer.play('F#', 4, 0.05)
            await self.buzzer.play('G', 4, 0.05)
            
class SessionHandler(HandlerBase):
    def __init__(self, context, notificator):
        self.context = context
        self.combination_pressed = False
        self.notificator = notificator

    async def reset_session(self):
        self.context.session_time = time.time()

    async def on_notify(self, button_status):
        vt, button_a, button_b, button_c, button_d = button_status

        self.combination_pressed = button_a is True and button_d is True

    async def run(self):
        await self.notificator.announce(ANNOUNCE_INIT)

        while True:
            delta = time.time() - self.context.session_time

            if self.context.session_active is True:

                if delta > SESSION_MAX_TIME:
                    print(f'Session expired')
                    await self.notificator.announce(ANNOUNCE_DISARM)
                    self.context.session_active = False

                if self.combination_pressed:
                    await asyncio.sleep(SESSION_MAX_TIME_PRESSED)
                    if self.combination_pressed:
                        print("Force exit session")
                        await self.notificator.announce(ANNOUNCE_DISARM)
                        self.context.session_active = False
            else:
                if self.combination_pressed:
                    print("Activating session")
                    await self.notificator.announce(ANNOUNCE_ARM)
                    self.context.session_time = time.time()
                    self.context.session_active = True
                
            await asyncio.sleep(SESSION_TIME_SLEEP)
            continue

    
class ControlHandler(HandlerBase):
    def __init__(self, context):
        self.context = context
        self.keyboard = Keyboard(usb_hid.devices)

        self.STATE_IDLE = 0
        self.STATE_PRESS = 1
        self.STATE_PRESSED = 2

        self.state = self.STATE_IDLE


    async def performSingleKeyStroke(self, keycode):
        self.keyboard.press(keycode)
        self.keyboard.release(keycode)

    async def on_notify(self, button_status):
        vt, button_a, button_b, button_c, button_d = button_status

        if self.context.session_active is False:
            return
        
        if self.state == self.STATE_IDLE:
            if vt is False:
                return

            if not (button_a ^ button_b ^ button_c ^ button_d):
                return

            print('Received uncombined keypress')

            if button_a:
                await self.performSingleKeyStroke(Keycode.SPACEBAR)
            elif button_b:
                await self.performSingleKeyStroke(Keycode.RIGHT_ARROW)
            elif button_c:
                await self.performSingleKeyStroke(Keycode.LEFT_ARROW)
            elif button_d:
                await self.performSingleKeyStroke(Keycode.P)

            self.state = self.STATE_PRESSED
    
        elif self.state == self.STATE_PRESSED:
            if vt is not True:
                print('Key depressed, changing state to idle')
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
        
    async def run(self):
        while True:
            await self.notify_observers()

            await asyncio.sleep(0.1)

            

    async def get_keystrokes(self):
        return (self.vt.value, self.button_a.value, self.button_b.value, self.button_c.value, self.button_d.value)

    async def subscribe_observer(self, observer):
        self.observers.append(observer)

    async def notify_observers(self):
        for observer in self.observers:
            await observer.on_notify(await self.get_keystrokes())
    
async def main(): 
    context = Context()
    notificator = Notificator()
    button_handler = ButtonHandler(context)
    session_handler = SessionHandler(context, notificator)
    control_handler = ControlHandler(context)

    await button_handler.subscribe_observer(session_handler)
    await button_handler.subscribe_observer(control_handler)

    session_task = asyncio.create_task(session_handler.run())
    button_handler_task = asyncio.create_task(button_handler.run())
    await asyncio.gather(session_task, button_handler_task)

def sendall(client, data):
    data = data.replace('  ', ' ')
    while len(data):
        # split data in chunks to avoid EAGAIN exception
        part = data[0:512]
        data = data[len(part):len(data)]
        print('Sending: ' + str(len(part)) + 'b')
        # EAGAIN too much data exception catcher
        while True:
            try:
                client.sendall(part)
            except OSError as e:
                print("Exception", str(e))
                time.sleep(0.25)
                pass
            break

def send_header(client, status_code=200, content_length=None):
    sendall(client, "HTTP/1.0 {} OK\r\n".format(status_code))
    sendall(client, "Content-Type: text/html\r\n")
    if content_length is not None:
        sendall(client, "Content-Length: {}\r\n".format(content_length))
    sendall(client, "\r\n")

def send_response(client, payload, status_code=200):
    content_length = len(payload)
    send_header(client, status_code, content_length)
    if content_length > 0:
        sendall(client, payload)
    client.close()

while True:
    # EAGAIN exception catcher
    print("f")
    while True:
        try:
            client, addr = server_socket.accept()
        except OSError as e:
            print("Exception", str(e))
            time.sleep(0.25)
            pass
        break

    print('Client connected - %s:%s' % addr)
    try:
        client.settimeout(5)

        request = b""
        try:
            while "\r\n\r\n" not in request:
                buffer = bytearray(512)
                client.recv_into(buffer, 512)
                request += buffer
                print('Received data')
        except OSError:
            pass

        # Handle form data from Safari on macOS and iOS; it sends \r\n\r\nssid=<ssid>&password=<password>
        try:
            buffer = bytearray(1024)
            client.recv_into(buffer, 1024)
            request += buffer
            print("Received form data after \\r\\n\\r\\n(i.e. from Safari on macOS or iOS)")
        except OSError:
            pass

        request = request.decode().strip("\x00").replace('%23', '#')

        print("Request is: {}".format(request))
        if "HTTP" not in request:  # skip invalid requests
            continue

        url = re.search("(?:GET|POST) (.*?)(?:\\?.*?)? HTTP", request).group(1)
        print("URL is {}".format(url))

        if url == "/":
            send_header(client)
            sendall(client, "hello world")

    finally:
        client.close()