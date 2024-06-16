# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2022 Howetuft <howetuft@gmail.com>                      *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

"""This module implements a framework for Render plugins.

A plugin is an applet that can be launched by Render in a separate process,
with an access to Render virtual environment.
"""
import sys
import os
import signal
import argparse
from multiprocessing.connection import Client, wait
from threading import Thread, Event
from dataclasses import dataclass


sys.path.append(os.getcwd())


# Partially parse command line
plugin_parser = argparse.ArgumentParser()
plugin_parser.add_argument(
    "--server",
    help="the communication server name",
    type=str,
)
plugin_parser.add_argument(
    "--pyside",
    help="pyside version",
    type=str,
    choices=("PySide2", "PySide6"),
)
PLUGIN_ARGS, ARGS = plugin_parser.parse_known_args()
PYSIDE = PLUGIN_ARGS.pyside
SERVERNAME = PLUGIN_ARGS.server

if PYSIDE == "PySide2":
    from PySide2.QtCore import QObject, QTimer, Slot, Signal, Qt, QThread
    from PySide2.QtWidgets import QApplication, QMainWindow, QMessageBox

if PYSIDE == "PySide6":
    from PySide6.QtCore import QObject, QTimer, Slot, Signal, Qt, QThread
    from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox


def debug(msg):
    """Show pop-up for debugging."""
    QMessageBox.information(None, "Debug", msg)


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(
                *args, **kwargs
            )
        return cls._instances[cls]


class Socket(object, metaclass=Singleton):
    """A socket to listen to parent process."""

    def __init__(self):
        """Initialize socket."""
        # Communication
        self._connection = Client(SERVERNAME)
        self._connection_active = Event()
        self._connection_active.set()

    def send(self, verb, argument):
        """Send message to parent process."""
        message = (verb, argument)
        self._connection.send(message)

    def recv(self):
        """Receive messages from parent process.

        Blocking till message or connection deactivation.
        """
        while self._connection_active.is_set():
            # We use wait to get a timeout parameter
            # and check periodically connection is active
            for conn in wait([self._connection], timeout=1):
                try:
                    message = conn.recv()
                except EOFError:
                    self._connection_active.clear()
                else:
                    return message
        else:
            return None

    def stop_recv(self):
        """Stop on-going receiving."""
        self._connection_active.clear()

    def __del__(self):
        """Finalize - stop on-going receiving if any."""
        self._connection_active.clear()


SOCKET = Socket()
signal.signal(signal.SIGTERM, signal.SIG_DFL)


class RenderPlugin(QApplication):
    """Plugin base class."""

    quit_signal = Signal()

    def __init__(self, widget, *args):
        """Initialize plugin."""
        super().__init__()

        # Application and window
        self.mainwindow = QMainWindow(flags=Qt.FramelessWindowHint)
        self.mainwindow.showMaximized()

        # Listen to entering messages
        self.quit_signal.connect(self.close_and_quit, Qt.QueuedConnection)
        self.listen_thread = QThread()
        setattr(self.listen_thread, "run", self.listen)
        self.listen_thread.finished.connect(self.listen_thread.deleteLater)
        self.listen_thread.start()

        # Central widget
        try:
            self.widget = widget(*args)
        except:
            print("WARNING: Could not initialize plugin central widget")
            self.widget = None
        else:
            self.widget.setParent(self.mainwindow)

    @Slot()
    def add_widget(self):
        """Add viewer (once application has been started)."""
        if self.widget:
            self.mainwindow.setCentralWidget(self.widget)
            self.widget.showMaximized()
            self.widget.setVisible(True)
        winid = self.mainwindow.winId()
        SOCKET.send("WINID", winid)

    def listen(self):
        """Listen to messages from the socket."""
        while message := SOCKET.recv():
            verb, argument = message
            if verb == "CLOSE":
                SOCKET.stop_recv()
                self.quit_signal.emit()
            else:
                cb_handle_message(message)

    def cb_handle_message(self, message):
        """Handle messages sent by parent process - Callback.

        To be overriden by subclass.
        """
        pass

    @Slot()
    def close_and_quit(self):
        """Gracefully quit application."""
        SOCKET.stop_recv()
        self.listen_thread.quit()
        if not self.listen_thread.wait(5000):
            # Occurs only if something went wrong...
            self.listen_thread.terminate()
        self.closeAllWindows()
        self.quit()

    def exec(self):
        """Execute application (start event loop)."""
        QTimer.singleShot(0, self.add_widget)
        if PYSIDE == "PySide2":
            return super().exec_()
        if PYSIDE == "PySide6":
            return super().exec()


@dataclass
class Bcolors:
    """Blender terminal colors."""

    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    COLOROFF = "\033[0m"


def log(msg):
    """Print message as log."""
    SOCKET.send("LOG", msg)


def msg(msg):
    """Print message as plain message."""
    SOCKET.send("MSG", msg)


def warn(msg):
    """Print message as warning."""
    SOCKET.send("WARN", msg)


def error(msg):
    """Print message as error."""
    SOCKET.send("ERROR", msg)
