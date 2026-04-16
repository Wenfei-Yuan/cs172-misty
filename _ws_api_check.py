#!/usr/bin/env python3
"""Quick check of websockets 16 API surface."""
import websockets

print("version:", websockets.__version__)

# RC-1: Does websockets.exceptions.ConnectionClosed work?
try:
    exc = websockets.exceptions.ConnectionClosed
    print("RC-1: websockets.exceptions.ConnectionClosed =", exc, "-> WORKS")
except AttributeError as e:
    print("RC-1: websockets.exceptions.ConnectionClosed -> FAILS:", e)

# Alternative: is it on the top-level?
try:
    exc2 = websockets.ConnectionClosed
    print("RC-1 alt: websockets.ConnectionClosed =", exc2, "-> WORKS")
except AttributeError as e:
    print("RC-1 alt: websockets.ConnectionClosed -> FAILS:", e)

# RC-2: Does ClientConnection have .open?
try:
    from websockets import ClientConnection
    print("RC-2: ClientConnection has .open?", hasattr(ClientConnection, "open"))
    relevant = [a for a in dir(ClientConnection) if not a.startswith("_")]
    print("RC-2: All public attrs:", relevant)
except Exception as e:
    print("RC-2:", e)

# RC-3: Does websockets.WebSocketClientProtocol exist?
try:
    p = websockets.WebSocketClientProtocol
    print("RC-3: websockets.WebSocketClientProtocol =", p, "-> WORKS")
except AttributeError as e:
    print("RC-3: websockets.WebSocketClientProtocol -> FAILS:", e)

# RC-3 alt: What about WebSocketServerProtocol?
try:
    p2 = websockets.WebSocketServerProtocol
    print("RC-3 alt: websockets.WebSocketServerProtocol =", p2, "-> WORKS")
except AttributeError as e:
    print("RC-3 alt: websockets.WebSocketServerProtocol -> FAILS:", e)

# Check serve and connect exist
print()
print("websockets.serve:", websockets.serve)
print("websockets.connect:", websockets.connect)

# Check if 'exceptions' is a submodule vs just re-exported names
print()
print("type(websockets.exceptions):", type(getattr(websockets, "exceptions", "MISSING")))
try:
    import websockets.exceptions as we
    print("import websockets.exceptions works:", we)
    print("  ConnectionClosed:", we.ConnectionClosed)
except Exception as e:
    print("import websockets.exceptions FAILS:", e)
