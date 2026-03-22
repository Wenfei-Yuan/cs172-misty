# Scripts Overview — cs172-misty

> This file summarizes all code scripts in the repository. Each entry includes a high-level summary, per-module breakdown (name/parameters/output + one-line description), and a list of dependencies.

---

## `main.py`

**Location:** repo root  
**Language:** Python  
**Role:** Entry point. Bootstraps the Misty robot connection by loading the IP address from environment variables and instantiating the `Misty` SDK client.

**High-level summary:**  
Reads `MISTY_IP_ADDRESS` from the `.env` file via `EnvLoader`, then constructs a `Misty(ip)` client object (`my_misty`). At runtime, the script currently only performs object construction — no robot commands are actually sent. A commented-out `get_info("device")` call serves as a connectivity sanity check template.

**Dependencies:**
- `misty2py.robot.Misty` — external SDK robot client class (REST + WebSocket API wrapper)
- `misty2py.utils.env_loader.EnvLoader` — external SDK utility for reading `.env` configuration
- `.env` file at runtime — must contain `MISTY_IP_ADDRESS`

---

### Module Breakdown

| Code Block | Signature / Call | Output | Description |
|---|---|---|---|
| Environment loader init | `EnvLoader()` → `env_loader` | `EnvLoader` instance | Instantiates the env loader with default `.env` path; reads all key-value pairs from `.env` (including `MISTY_IP_ADDRESS`) into memory. |
| IP extraction + Misty init | `Misty(env_loader.get_ip())` → `my_misty` | `Misty` instance | Calls `get_ip()` on the loader (returns `Optional[str]`), passes the IP to `Misty.__init__`; constructs the robot client wired to the physical Misty II over the local network. |
| *(commented)* Connection test | `my_misty.get_info("device")` → `response` | `Misty2pyResponse` | Sends a REST GET request to the robot's `/api/device` endpoint; returns device metadata — useful to verify the IP and network connection are correct. Not currently active. |
| *(commented)* Response parse | `response.parse_to_dict()` → `dict` | `dict` | Deserializes the JSON response from the robot into a Python dictionary for inspection/printing. Not currently active. |

---

### Active vs. Commented Code

| Status | Code | Purpose |
|---|---|---|
| **Active** | `EnvLoader()` | Load environment config |
| **Active** | `Misty(env_loader.get_ip())` | Instantiate robot client |
| **Commented out** | `my_misty.get_info("device")` | Verify robot connection |
| **Commented out** | `print(response.parse_to_dict())` | Inspect API response |

---

## External SDK Reference (misty2py)

The following are external package modules imported by `main.py`. They are not part of the repo source but are documented here for context.

### `misty2py.utils.env_loader.EnvLoader`

| Method | Signature | Output | Description |
|---|---|---|---|
| `__init__` | `(env_path: str = ".env") → None` | None | Loads all key-value pairs from the specified `.env` file into `self.values` using `python-dotenv`. |
| `get_ip` | `() → Optional[str]` | `str` or `None` | Returns `self.values.get("MISTY_IP_ADDRESS")`; extracts the robot's IP address, or `None` if the key is missing. |

### `misty2py.robot.Misty`

| Method | Signature | Output | Description |
|---|---|---|---|
| `__init__` | `(ip, custom_info={}, custom_actions={}, custom_data={}, rest_protocol="http", websocket_protocol="ws", websocket_endpoint="pubsub") → None` | None | Stores IP, constructs `Info`, `Action`, and `MistyEventHandler` sub-objects; initializes all three communication channels. |
| `__str__` | `() → str` | `str` | Returns a human-readable string with the robot's IP; useful for debugging. |
| `get_info` | `(info_name: str, params: Dict = {}) → Misty2pyResponse` | `Misty2pyResponse` | Sends a REST GET request for the named endpoint (e.g., `"device"`, `"battery"`); returns structured robot state data. |
| `perform_action` | `(action_name: str, data: Dict = {}) → Misty2pyResponse` | `Misty2pyResponse` | Sends a REST POST/PUT request for the named action (e.g., move arm, play audio); executes a robot command. |
| `event` | `(action: str, **kwargs) → Misty2pyResponse` | `Misty2pyResponse` | Manages WebSocket event subscriptions; supports `"subscribe"`, `"get_data"`, `"get_log"`, `"unsubscribe"` lifecycle actions. |

---

> **Note:** As the project grows, new Python modules and behavior scripts will be added. This file should be updated accordingly via the initialize or code workflows.
