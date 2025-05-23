#!/usr/bin/env python3
# tdsr, a terminal screen reader
# Copyright (C) 2016, 2017  Tyler Spivey
# See the license in LICENSE file

import argparse
import codecs
import configparser
import copy
import fcntl
import importlib
from io import StringIO, SEEK_END
import logging
import os
import platform
import pyte
import re
import select
from setproctitle import setproctitle
import shlex
import signal
import struct
import subprocess
import sys
import termios
import time
import tty
from wcwidth import wcwidth

logger = logging.getLogger("tdsr")
logger.addHandler(logging.NullHandler())

TDSR_DIR = os.path.abspath(os.path.dirname(os.path.realpath(__file__)))
DEFAULT_CONFIG = os.path.join(TDSR_DIR, 'tdsr.cfg.dist')
# start using XDG in the future, but if the file already exists in ~/.tdsr.cfg, respect it
CONFIG_FILE = os.path.expanduser('~/.tdsr.cfg')
if not os.path.exists(CONFIG_FILE):
    CONFIG_FILE = os.path.expanduser('~/.config/tdsr/tdsr.cfg')

CURSOR_TIMEOUT = 0.02
REPEAT_KEY_TIMEOUT = 0.5
PHONETICS = {x[0]: x for x in [
        'alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot',
        'golf', 'hotel', 'india', 'juliet', 'kilo', 'lima', 'mike',
        'november', 'oscar', 'papa', 'quebec', 'romeo', 'sierra',
        'tango', 'uniform', 'victor', 'wiskey', 'x ray', 'yankee',
        'zulu']
}

class State:
    def __init__(self):
        self.revy = 0
        self.revx = 0
        self.delayed_functions = []
        self.silence = False
        self.tempsilence = False
        self.key_handlers = []
        self.config = configparser.ConfigParser()
        self.config['speech'] = {'process_symbols': 'false', 'key_echo': True, 'cursor_tracking': True,
        "line_pause": True, 'repeated_symbols': 'false', 'repeated_symbols_values': '-=!#'}
        self.config['symbols'] = {}
        self.config['plugins'] = {}
        self.symbols_re = None
        self.copy_x = None
        self.copy_y = None
        self.last_drawn_x = 0
        self.last_drawn_y = 0
        self.delaying_output = False

    def save_config(self):
        with open(CONFIG_FILE, 'w') as fp:
            self.config.write(fp)

    def build_symbols_re(self):
        candidates = []
        for symbol in self.config['symbols']:
            symbol = int(symbol)
            if symbol == 32:
                continue
            candidates.append(re.escape(chr(symbol)))
        if not candidates:
            return None
        return re.compile('|'.join(candidates))

class KeyHandler:
    PASSTHROUGH = 1
    REMOVE = 2
    def __init__(self, keymap, fd=None):
        self.keymap = keymap
        self.fd = fd
        self.last_key = None
        self.last_key_time = 0.0

    def add(self, key, handler):
        if key not in self.keymap:
            self.keymap[key] = handler

    def process(self, data):
        key_time = time.monotonic()
        key_delta = key_time - self.last_key_time
        self.last_key_time = key_time
        repeat = data + data
        if data not in self.keymap:
            self.last_key = data
            return self.handle_unknown_key(data)
        elif self.last_key == data and repeat in self.keymap and key_delta <= REPEAT_KEY_TIMEOUT:
            result = self.keymap[repeat]()
            self.last_key = data
            return result
        else:
            result = self.keymap[data]()
            if result == KeyHandler.PASSTHROUGH:
                os.write(self.fd, data)
            self.last_key = data
            return result

    def handle_unknown_key(self, data):
        os.write(self.fd, data)

class ConfigHandler(KeyHandler):
    def __init__(self):
        self.keymap = {
            b'r': self.set_rate,
            b'v': self.set_volume,
            b'V': self.set_voice_idx,
            b'p': self.set_process_symbols,
            b'd': self.set_delay,
            b'e': self.set_echo,
            b'c': self.set_cursor_tracking,
            b'l': self.set_line_pause,
            b's': self.set_repeated_symbols,
        }
        super().__init__(self.keymap)

    def set_rate(self):
        say("Rate")
        state.key_handlers.append(BufferHandler(on_accept=self.set_rate2))

    def set_rate2(self, val):
        try:
            val = int(val)
        except ValueError:
            say("Invalid value")
            return
        synth.set_rate(val)
        state.config['speech']['rate'] = str(val)
        state.save_config()
        say("Confirmed")

    def set_volume(self):
        say("volume")
        state.key_handlers.append(BufferHandler(on_accept=self.set_volume2))

    def set_volume2(self, val):
        try:
            val = int(val)
        except ValueError:
            say("Invalid value")
            return
        synth.set_volume(val)
        state.config['speech']['volume'] = str(val)
        state.save_config()
        say("Confirmed")

    def set_voice_idx(self):
        say("voice index")
        state.key_handlers.append(BufferHandler(on_accept=self.set_voice_idx2))

    def set_voice_idx2(self, val):
        try:
            val = int(val)
        except ValueError:
            say("Invalid value")
            return
        synth.set_voice_idx(val)
        state.config['speech']['voice_idx'] = str(val)
        state.save_config()
        say("Confirmed")


    def set_process_symbols(self):
        current = state.config.getboolean('speech', 'process_symbols', fallback=False)
        current = not current
        state.config['speech']['process_symbols'] = str(current)
        state.save_config()
        say("process symbols on" if current else "process symbols off")

    def set_echo(self):
        current = state.config.getboolean('speech', 'key_echo', fallback=True)
        current = not current
        state.config['speech']['key_echo'] = str(current)
        state.save_config()
        say("character echo on" if current else "character echo off")

    def set_cursor_tracking(self):
        current = state.config.getboolean('speech', 'cursor_tracking', fallback=True)
        current = not current
        state.config['speech']['cursor_tracking'] = str(current)
        state.save_config()
        say("cursor tracking on" if current else "cursor tracking off")

    def set_line_pause(self):
        current = state.config.getboolean('speech', 'line_pause')
        current = not current
        state.config['speech']['line_pause'] = str(current)
        state.save_config()
        say("line pause on" if current else "line pause off")

    def set_repeated_symbols(self):
        current = state.config.getboolean('speech', 'repeated_symbols', fallback=False)
        current = not current
        state.config['speech']['repeated_symbols'] = str(current)
        state.save_config()
        say("repeated symbols on" if current else "repeated symbols off")

    def set_delay(self):
        say("Cursor delay")
        state.key_handlers.append(BufferHandler(on_accept=self.set_delay2))

    def set_delay2(self, val):
        global CURSOR_TIMEOUT
        try:
            val = int(val)
        except ValueError:
            say("Invalid value")
            return
        val/=1000
        CURSOR_TIMEOUT = val
        state.config['speech']['cursor_delay'] = str(val)
        state.save_config()
        say("Confirmed")

    def handle_unknown_key(self, data):
        if data == b'\r' or data == b'\n':
            say("exit")
            return KeyHandler.REMOVE

class CopyHandler(KeyHandler):
    def __init__(self):
        keymap = {
            b'l': self.copy_line,
            b's': self.copy_screen,
        }
        super().__init__(keymap)

    def copy_line(self):
        try:
            copy_text(state.revy, 0, state.revy, screen.columns-1)
        except:
            say("Failed")
        else:
            say("line")
        return KeyHandler.REMOVE

    def copy_screen(self):
        try:
            copy_text(0, 0, screen.lines - 1, screen.columns - 1)
        except:
            say("Failed")
        else:
            say("screen")
        return KeyHandler.REMOVE

    def handle_unknown_key(self, data):
        say("unknown key")
        return KeyHandler.REMOVE

class BufferHandler(KeyHandler):
    def __init__(self, on_accept=None):
        self.on_accept = on_accept
        self.buffer = ''
        super().__init__(keymap={})

    def process(self, data):
        if data == b'\r' or data == b'\n':
            self.on_accept(self.buffer)
            return KeyHandler.REMOVE
        else:
            self.buffer += data.decode('utf-8')

class Synth:
    def __init__(self, speech_server):
        self.pipe = None
        self.speech_server = speech_server
        self.rate = None
        self.volume = None
        self.voice_idx = None

    def start(self):
        self.pipe = subprocess.Popen(self.speech_server, stdin=subprocess.PIPE)
        if self.rate is not None:
            self.set_rate(self.rate)
        if self.volume is not None:
            self.set_volume(self.volume)
        if self.voice_idx is not None:
            self.set_voice_idx(self.voice_idx)

    def send(self, text):
        text = text.encode('utf-8')
        try:
            self.pipe.stdin.write(text)
            self.pipe.stdin.flush()
        except BrokenPipeError:
            try:
                self.start()
                self.pipe.stdin.write(text)
                self.pipe.stdin.flush()
            except BrokenPipeError:
                pass # We'll try to restart next time something is sent

    def set_rate(self, rate):
        self.rate = rate
        self.send('r%d\n' % rate)

    def set_volume(self, volume):
        self.volume = volume
        self.send('v%d\n' % volume)

    def set_voice_idx(self, voice_idx):
        self.voice_idx = voice_idx
        self.send('V%d\n' % voice_idx)

    def close(self):
        self.pipe.stdin.close()
        self.pipe.wait()

state = State()
speech_buffer = StringIO()
lastkey = ""

def main():
    # setup old variable
    try:
        old = termios.tcgetattr(0)
        tty.setraw(0)
    finally:
        termios.tcsetattr(0, termios.TCSADRAIN, old)

    os.environ["TSDR_ACTIVE"] = "true"
    global CURSOR_TIMEOUT, signal_pipe

    # argparser stuff
    parser = argparse.ArgumentParser()
    parser.add_argument('-s', '--speech-server', action='store', help='speech server command to run')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('program', action='store', nargs='*')
    args = parser.parse_args()

    if args.speech_server is None:
        if platform.system() == 'Darwin':
            speech_server = 'tdsr-mac'
        else:
            # Works on Linux, hopefully other places too:
            speech_server = os.path.join(TDSR_DIR, 'speechdispatcher.py')
    else:
        speech_server = shlex.split(args.speech_server)

    if args.debug:
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        fh = logging.FileHandler('tdsr.log')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        logger.debug("TDSR started")

    rows, cols = get_terminal_size(0)
    global synth, screen
    synth = Synth(speech_server)
    synth.start()

    if os.path.exists(DEFAULT_CONFIG) and not os.path.exists(CONFIG_FILE):
        state.config.read(DEFAULT_CONFIG)
        state.save_config()

    state.config.read(CONFIG_FILE)
    state.symbols_re = state.build_symbols_re()
    if 'rate' in state.config['speech']:
        synth.set_rate(int(state.config['speech']['rate']))
    if 'volume' in state.config['speech']:
        synth.set_volume(int(state.config['speech']['volume']))
    if 'voice_idx' in state.config['speech']:
        synth.set_voice_idx(int(state.config['speech']['voice_idx']))
    if 'cursor_delay' in state.config['speech']:
        CURSOR_TIMEOUT = float(state.config['speech']['cursor_delay'])
    pid, fd = os.forkpty()
    if pid == 0:
        handle_child(args)
    screen = MyScreen(cols, rows)
    pyte.Stream.csi['S'] = 'scroll_up'
    pyte.Stream.csi['T'] = 'scroll_down'
    stream = pyte.Stream()
    stream.attach(screen)
    screen.define_charset('B', '(')
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    default_key_handler = KeyHandler(keymap, fd)
    state.key_handlers.append(default_key_handler)
    plugins = dict(state.config.items('plugins'))
    for plugin, shortcut in plugins.items():
        default_key_handler.add(f'\x1b{shortcut}'.encode(), handle_plugin(plugin))

    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    resize_terminal(fd)
    signal_pipe = os.pipe()
    signal.signal(signal.SIGWINCH, handle_sigwinch)
    say("tdsr, presented by Lighthouse of San Francisco")
    while True:
        r, w, e = select.select([sys.stdin, fd, signal_pipe[0]], [], [], time_until_next_delayed())
        if signal_pipe[0] in r:
            os.read(signal_pipe[0], 1)
            old_x, old_y = screen.cursor.x, screen.cursor.y
            rows, cols = resize_terminal(fd)
            screen.resize(rows, cols)
            state.revx = min(state.revx, cols - 1)
            state.revy = min(state.revy, rows - 1)
            screen.cursor.x = min(old_x, cols - 1)
            screen.cursor.y = min(old_y, rows - 1)
        if sys.stdin in r:
            bytes = os.read(0, 4096)
            process_input(bytes, fd)
        if fd in r:
            try:
                bytes = read_all(fd)
            except (EOFError, OSError):
                sb()
                synth.close()
                sys.exit(0)
            decoded_bytes = decoder.decode(bytes)
            x, y = screen.cursor.x, screen.cursor.y
            stream.feed(decoded_bytes)
            if state.config.getboolean('speech', 'cursor_tracking') and (screen.cursor.x != x or screen.cursor.y != y):
                state.revx, state.revy = screen.cursor.x, screen.cursor.y
            if not state.silence and not state.tempsilence:
                if speech_buffer.tell() > 0 and not state.delaying_output:
                    schedule(0.005, read_buffer_scheduled)
            os.write(1, bytes)
        run_scheduled()

def read_buffer_scheduled():
    state.delaying_output = False
    sb()

def read_all(fd):
    bytes = os.read(fd, 4096)
    if bytes == b'':
        raise EOFError
    while has_more(fd):
        data = os.read(fd, 4096)
        if data == b'':
            raise EOFError
        bytes += data
    return bytes

def handle_child(args):
    if args.program:
            program = shlex.split(" ".join(args.program))
    else:
        program = [os.environ["SHELL"]]
    os.execv(program[0], program)

newbytes = StringIO()
in_escape = False
escsec = ""
def process_input(bytes, fd):
    global lastkey
    # Busybox ash asks the terminal to send the cursor position, ignore it
    if re.match(br'\033\[\d+;\d+R', bytes):
        os.write(fd, bytes)
        return
    lastkey = ""
    silence()
    state.delayed_functions = []
    state.tempsilence = False
    res = state.key_handlers[-1].process(bytes)
    if res == KeyHandler.REMOVE:
        state.key_handlers = state.key_handlers[:-1]
    lastkey = bytes.decode('utf-8', 'replace')

def handle_backspace():
    x = screen.cursor.x
    if x > 0:
        say_character(screen.buffer[screen.cursor.y][screen.cursor.x - 1].data)
    return KeyHandler.PASSTHROUGH

def handle_delete():
    x = screen.cursor.x
    say_character(screen.buffer[screen.cursor.y][screen.cursor.x].data)
    return KeyHandler.PASSTHROUGH

def handle_plugin(plugin_name):
    try:
        mod = importlib.import_module(f"plugins.{plugin_name}")
    except Exception as e:
        logger.error(e)
        say(f"Error loading plugin {e.args}")

    prompt_matcher = re.compile(fr"{state.config.get('speech', 'prompt', fallback='.*')}")
    command = state.config.get('commands', plugin_name, fallback=None)
    check_command = command is not None
    if command:
        command_matcher = re.compile(command)

    def handle():
        lines = []
        for i in reversed(range(len(screen.buffer))):
            line = "".join(screen.buffer[i][x].data for x in range(screen.columns)).strip()
            lines.append(line)
            if prompt_matcher.search(line) and check_command and command_matcher.search(line):
                break
        try:
            lines_to_read = mod.parse_output(lines)
            for line in lines_to_read:
                say(line)
        except Exception as e:
            logger.error(e)
            say(f"Error loading plugin {e.args}")

    return handle

def sayline(y):
    line = "".join(screen.buffer[y][x].data for x in range(screen.columns)).strip()
    if line == u'':
        line = u'blank'

    new_line = replace_duplicate_characters_with_count(line)

    say(new_line)


def replace_duplicate_characters_with_count(line):
    symbols = state.config.get('speech', 'repeated_symbols_values', fallback='-=!#')
    matcher = re.compile(fr'([{symbols}])\1*')
    new_line = line
    if state.config.getboolean('speech', 'repeated_symbols'):
        results = matcher.finditer(line)
        for r in results:
            match = r.group()
            if len(match) > 1:
                new_line = new_line.replace(match, f"{len(match)} {r.groups()[0]}", 1)

    return new_line


def prevline():
    state.revy -= 1
    if state.revy < 0:
        say("top")
        state.revy = 0
    sayline(state.revy)

def nextline():
    state.revy += 1
    if state.revy > screen.lines - 1:
        say("bottom")
        state.revy = screen.lines - 1
    sayline(state.revy)

def prevchar():
    state.revx -= 1
    if state.revx < 0:
        say("left")
        state.revx = 0
    skip_to_previous_char()
    saychar(state.revy, state.revx)

def saychar(y, x, phonetically=False):
    char = screen.buffer[y][x].data
    lchar = char.lower()
    if phonetically and lchar in PHONETICS:
        synth.send('s%s\n' % PHONETICS[lchar])
    else:
        say_character(char)

def nextchar():
    state.revx += wcwidth(screen.buffer[state.revy][state.revx].data)
    if state.revx > screen.columns - 1:
        say("right")
        state.revx = screen.columns - 1
        skip_to_previous_char()
    saychar(state.revy, state.revx)

def skip_to_previous_char():
    while screen.buffer[state.revy][state.revx].data == '':
        state.revx -= 1

def topOfScreen():
    state.revy = 0
    sayline(state.revy)

def bottomOfScreen():
    state.revy = screen.lines - 1
    sayline(state.revy)

def startOfLine():
    state.revx = 0
    saychar(state.revy, state.revx)

def endOfLine():
    state.revx = screen.columns - 1
    saychar(state.revy, state.revx)

class MyScreen(pyte.Screen):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved_cursor = None
        self.saved_buffer = None

    def set_margins(self, top=None, bottom=None):
        if top == 0 and bottom is None:
            top = None
        super().set_margins(top=top, bottom=bottom)

    def draw(self, text):
        for c in text:
            self.draw2(c)

    def draw2(self, text):
        global lastkey
        logger.debug("Drawing %r", text)
        add_to_buffer = True
        if text == lastkey:
            add_to_buffer = False
            if len(text) == 1 and state.config.getboolean('speech', 'key_echo', fallback=False):
                say_character(text)
        lastkey = ""
        if screen.cursor.y == state.last_drawn_y and screen.cursor.x - state.last_drawn_x > 1:
            speech_buffer.write(' ')
        super(MyScreen, self).draw(text)
        if add_to_buffer and not state.silence and not state.tempsilence:
            speech_buffer.write(text)
            state.last_drawn_x = screen.cursor.x
            state.last_drawn_y = screen.cursor.y

    def tab(self):
        if not state.silence:
            speech_buffer.write(' ')
        super().tab()

    def linefeed(self):
        if state.config.getboolean('speech', 'line_pause'):
            sb()
        else:
            speech_buffer.seek(0, SEEK_END)
            speech_buffer.write(' ')
        super(MyScreen, self).linefeed()

    def backspace(self):
        if self.cursor.x > 0 and speech_buffer.tell() > 0:
            speech_buffer.seek(speech_buffer.tell() - 1)
        super(MyScreen, self).backspace()

    def reset_mode(self, *modes, **kwargs):
        if 3 in modes:
            modes = list(modes)
            modes.remove(3)
        if 1049 in modes and self.saved_cursor is not None:
            self.cursor = self.saved_cursor
            self.buffer = self.saved_buffer
            self.saved_cursor = None
            self.saved_buffer = None
            self.dirty.update(range(self.lines))
        super().reset_mode(*modes, **kwargs)

    def set_mode(self, *modes, **kwargs):
        if 3 in modes:
            modes = list(modes)
            modes.remove(3)
        if 1049 in modes:
            self.saved_cursor = copy.copy(self.cursor)
            self.saved_buffer = copy.deepcopy(self.buffer)
            self.dirty.update(range(self.lines))
            self.cursor_position()
            self.buffer.clear()
        super().set_mode(*modes, **kwargs)

    def erase_in_display(self, how=0, private=False):
        if how == 3:
            return
        super().erase_in_display(how, private=private)

    def select_graphic_rendition(self, *attrs, **kwargs):
        if 'private' in kwargs:
            return
        return super().select_graphic_rendition(*attrs)

    def scroll_down(self,    count):
        if count == 0:
            count = 1
        top,    bottom = self.margins or pyte.screens.Margins(0, self.lines - 1)
        y = self.cursor.y
        self.cursor.y = top
        for i in range(count):
            self.reverse_index()
        self.cursor.y = y

    def scroll_up(self, count):
        if count == 0:
            count = 1
        top, bottom = self.margins or pyte.screens.Margins(0, self.lines - 1)
        y = self.cursor.y
        self.cursor.y = bottom
        for i in range(count):
            self.index()
        self.cursor.y = y

def sb():
    data = speech_buffer.getvalue()
    speech_buffer.truncate(0)
    speech_buffer.seek(0)
    if data == u'':
        return
    if not state.tempsilence:
        new_line = replace_duplicate_characters_with_count(data)
        say(new_line)

def say(data, force_process_symbols=False):
    data = data.strip()
    def replace_symbols(m):
        return " %s " % state.config['symbols'][str(ord(m.group(0)))]
    if state.symbols_re is not None and (force_process_symbols or state.config.getboolean('speech', 'process_symbols', fallback=False)):
        data = state.symbols_re.sub(replace_symbols, data)
    synth.send("s" + data + "\n")

def silence():
    synth.send('x\n')

def say_character(ch):
    if ch == '':
        ch = ' '
    key = str(ord(ch))
    if key in state.config['symbols']:
        synth.send('s%s\n' % state.config['symbols'][key])
    else:
        synth.send('l%s\n' % ch)

def arrow_up():
    schedule(CURSOR_TIMEOUT, (lambda: sayline(screen.cursor.y)), True)
    return KeyHandler.PASSTHROUGH

def arrow_down():
    schedule(CURSOR_TIMEOUT, (lambda: sayline(screen.cursor.y)), True)
    return KeyHandler.PASSTHROUGH

def arrow_left():
    schedule(CURSOR_TIMEOUT, (lambda: saychar(screen.cursor.y, screen.cursor.x)), True)
    return KeyHandler.PASSTHROUGH

def arrow_right():
    schedule(CURSOR_TIMEOUT, (lambda: saychar(screen.cursor.y, screen.cursor.x)), True)
    return KeyHandler.PASSTHROUGH

def schedule(timeout, func, set_tempsilence=False):
    state.delayed_functions.append((time.monotonic() + timeout, func))
    if set_tempsilence:
        state.tempsilence = True

def run_scheduled():
    if not state.delayed_functions:
        return
    to_remove = []
    curtime = time.monotonic()
    for i in state.delayed_functions:
        t, f = i
        if curtime >= t:
            f()
            to_remove.append(i)
    for i in to_remove:
        state.delayed_functions.remove(i)

def time_until_next_delayed():
    if not state.delayed_functions:
        return None
    return max(0, state.delayed_functions[0][0] - time.monotonic())

def config():
    say("config")
    state.key_handlers.append(ConfigHandler())

def copy_mode():
    say("copy")
    state.key_handlers.append(CopyHandler())

def get_char():
    return screen.buffer[state.revy][state.revx].data

def move_prevchar():
    if state.revx == 0:
        if state.revy == 0:
            return ''
        state.revy -= 1
        state.revx = screen.columns - 1
    else:
        state.revx -= 1

def move_nextchar():
    if state.revx == screen.columns - 1:
        if state.revy == screen.lines - 1:
            return ''
        state.revy += 1
        state.revx = 0
    else:
        state.revx += 1

def prevword():
    if state.revx == 0:
        say("left")
        sayword()
        return
    #Move over any existing word we might be in the middle of
    while state.revx > 0 and get_char() != ' ':
        move_prevchar()
    #Skip whitespace
    while state.revx > 0 and get_char() == ' ':
        move_prevchar()
    #Move to the beginning of the word we're now on
    while state.revx > 0 and get_char() != ' ' and screen.buffer[state.revy][state.revx - 1].data != ' ':
        move_prevchar()
    sayword()

def sayword(spell=False):
    word = ""
    revx, revy = state.revx, state.revy
    b = screen.buffer
    while state.revx > 0 and get_char() != ' ' and b[state.revy][state.revx - 1].data != ' ':
        move_prevchar()
    if state.revx == 0 and get_char() == ' ':
        say("space")
        return
    word += get_char()
    while state.revx < screen.columns - 1:
        move_nextchar()
        if get_char() == ' ':
            break
        word += get_char()
    if spell:
        say(' '.join(word), force_process_symbols=True)
    else:
        say(word)
    state.revx, state.revy = revx, revy

def nextword():
    revx, revy = state.revx, state.revy
    m = screen.columns - 1
    #Move over any existing word we might be in the middle of
    while state.revx < m and get_char() != ' ':
        move_nextchar()
    #Skip whitespace
    while state.revx < m and get_char() == ' ':
        move_nextchar()
    if state.revx == m and get_char() == ' ':
        say("right")
        state.revx = revx
        sayword()
        return
    sayword()

def handle_silence():
    state.silence = not state.silence
    say("quiet on" if state.silence else "quiet off")

def handle_clipboard():
    if state.copy_x is None:
        state.copy_x = state.revx
        state.copy_y = state.revy
        say("select")
        return
    end_x, end_y = state.revx, state.revy
    try:
        copy_text(state.copy_y, state.copy_x, end_y, end_x)
    except:
        say("Failed")
    else:
        say("copied")
    state.copy_x = None

def copy_text(start_y, start_x, end_y, end_x):
    if start_x > end_x:
        start_x, end_x = end_x, start_x
    if start_y > end_y:
        start_y, end_y = end_y, start_y
    display = screen.display
    buf = []
    start = start_x
    for y in range(start_y, end_y + 1):
        if y < end_y:
            end = screen.columns - 1
        else:
            end = end_x
        if y > start_y:
            start = 0
        chars = []
        for x in range(start, end + 1):
            chars.append(screen.buffer[y][x].data)
        buf.append("".join(chars).rstrip())
    buf = "\n".join(buf)
    copy_to_clip(buf)

def copy_to_clip(data):
    data = data.encode('utf-8')
    proc_args = []
    if platform.system() == 'Darwin':
        proc_args = ['pbcopy']
    elif platform.system() == 'Linux':
        if os.getenv('XDG_SESSION_TYPE') == 'wayland':
            proc_args = ['wl-copy']
        else:
            proc_args = ['xclip', '-selection', 'clip']

    if proc_args:
        proc = subprocess.Popen(proc_args, stdin=subprocess.PIPE)
        proc.stdin.write(data)
        proc.stdin.close()

def has_more(fd):
    r, w, e = select.select([fd], [], [], 0)
    if fd in r:
        return True
    return False

def get_terminal_size(fd):
    s = struct.pack('HHHH', 0, 0, 0, 0)
    rows, cols, _, _ = struct.unpack('HHHH', fcntl.ioctl(fd, termios.TIOCGWINSZ, s))
    return rows, cols

def resize_terminal(fd):
    s = struct.pack('HHHH', 0, 0, 0, 0)
    s = fcntl.ioctl(0, termios.TIOCGWINSZ, s)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, s)
    rows, cols, _, _ = struct.unpack('hhhh', s)
    return rows, cols

def handle_sigwinch(*args):
    os.write(signal_pipe[1], b'w')

keymap = {
    b'\x1bi': lambda: sayline(state.revy),
    b'\x1bu': prevline,
    b'\x1bo': nextline,
    b'\x1bj': prevword,
    b'\x1bk': sayword,
    b'\x1bk\x1bk': lambda: sayword(spell=True),
    b'\x1bl': nextword,
    b'\x1bm': prevchar,
    b'\x1b,': lambda: saychar(state.revy, state.revx),
    b'\x1b,\x1b,': lambda: saychar(state.revy, state.revx, phonetically=True),
    b'\x1b.': nextchar,
    b'\x1bU': topOfScreen,
    b'\x1bO': bottomOfScreen,
    b'\x1bM': startOfLine,
    b'\x1b>': endOfLine,
    # For the Hungarian keyboard layout
    b'\x1b:': endOfLine,
    b'\x1bc': config,
    b'\x1bq': handle_silence,
    b'\x1br': handle_clipboard,
    b'\x1bv': copy_mode,
    b'\x1bx': silence,
    b'\x08': handle_backspace,
    b'\x7f': handle_backspace,
    b'\x1b[3~': handle_delete,
    b'\x1b[A': arrow_up,
    b'\x1b[B': arrow_down,
    b'\x1b[C': arrow_right,
    b'\x1b[D': arrow_left,
    #
    b'\x1bOA': arrow_up,
    b'\x1bOB': arrow_down,
    b'\x1bOC': arrow_right,
    b'\x1bOD': arrow_left,
}

if __name__ == '__main__':
    try:
        setproctitle('tdsr')
    except Exception as e:
        print(e)
        pass

    main()
