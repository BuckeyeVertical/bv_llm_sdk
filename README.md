# LLM controlled drone SDK
## Setup
### Prereqs
Docker Desktop, Rust, uv, Python 3.12

### Step 1: Setup project structure
create a repo to work in 
```bash
mkdir ~/ws
```
Clone bv_bevy
```bash
cd ~/ws
git clone https://github.com/BuckeyeVertical/bv_bevy.git
```
Clone this repo (bv_llm_sdk)
```bash
cd ~/ws
git clone https://github.com/BuckeyeVertical/bv_llm_sdk.git
```
Should be in this layout:
```text
ws/
├── bv_bevy/
└── bv_llm_sdk/
```

### Step 2: Build dev environment
Install python venv deps
```bash
cd ~/ws/bv_llm_sdk
uv sync
```
Note: this project uses uv as a package manager so if your adding deps use ```uv add ...```

Set up bv_bevy (Docker or OrbStack, Rust and the build) by following its
[Setup](https://github.com/BuckeyeVertical/bv_bevy#setup) steps; you already cloned it into
`~/ws/bv_bevy`.

## Run simulation

Start PX4 + Gazebo first:
```bash
cd ~/ws/bv_bevy
./px4.sh --prebuilt forest
```

In a separate terminal, start Bevy (the 3D world and camera):
```bash
cd ~/ws/bv_bevy
cargo run --release -- forest
```

Check the toolkit can see the drone:
```bash
cd ~/ws/bv_llm_sdk
uv run scripts/tool.py get_status
```

## The toolkit

Every tool is a plain Python function that takes a connected `drone` first.
Open one connection and pass it to every tool:

```python
from bv_llm_sdk.toolkits.drone import connect, takeoff, move, turn, orbit, return_to_launch

with connect() as drone:
    takeoff(drone, altitude_m=10)
    move(drone, north_m=20)
    turn(drone, degrees=90)
    orbit(drone, radius_m=8, laps=1)
    return_to_launch(drone)
```

Tools that move the drone **block until the drone has actually arrived and
settled**, not just until PX4 accepts the command, so they chain safely: each
step starts only after the last one is done, and the drone hovers in between.
Each tool returns a dict (usually the new position) and raises `DroneError`
with a readable reason when it can't do what was asked (including PX4's own
explanation, e.g. "Arming denied: ...").

| Tool | What it does |
| --- | --- |
| `get_status` | Armed?, flight mode, on ground/in air, position, heading, speed, battery, GPS |
| `get_position` | Lat/lon, altitude above launch, metres north/east of home |
| `get_battery`, `get_home` | Battery %, voltage, estimated flight minutes left; home position |
| `get_distance_to(latitude, longitude)` | Distance and bearing to a point |
| `get_messages` | Recent autopilot warnings / refusal reasons |
| `preflight_check` | Ready to arm? GPS, battery, sensor health, home set |
| `arm`, `disarm` | Motors on/off (on the ground). `takeoff` arms by itself |
| `takeoff(altitude_m)` | Arm if needed, climb, hover |
| `land` | Land here, wait for touchdown |
| `return_to_launch(wait_for_landing)` | Fly home and land |
| `hold` | Stop and hover (also cancels orbit / RTL) |
| `move(north_m, east_m, up_m)` | Fly an offset in compass directions |
| `move_relative(forward_m, right_m, up_m)` | Fly an offset relative to where the drone faces |
| `goto_location(latitude, longitude, altitude_m)` | Fly to GPS coordinates |
| `set_altitude(altitude_m)` | Climb/descend in place |
| `set_heading(heading_deg)`, `turn(degrees)` | Rotate in place (absolute / relative) |
| `set_speed(speed_m_s)` | Horizontal speed for later moves |
| `orbit(radius_m, speed_m_s, laps, clockwise, center_latitude, center_longitude)` | Circle a point facing it |
| `wait(seconds)` | Hover for a while |
| `take_photo` | Save a camera image to `photos/` |
| `look` | Hand the current camera image to the LLM to look at itself |
| `detect_objects(object_name, min_confidence, color)` | Run YOLO on the current view; GPS position of each object |
| `search_for_object(object_name, distance_m, heading_deg, speed_m_s)` | Fly a line scanning for an object, stop over it |
| `search_area(object_name, north_m, east_m, speed_m_s, altitude_m, start_place, plan_only)` | Lawnmower-search a rectangle, stop over the object |
| `fly_to_object(object_name, altitude_m)` | Hover directly above a visible object |
| `survey_area(north_m, east_m, object_name, color, save_photos, altitude_m, start_place, plan_only)` | Fly a whole area and count/map every object (doesn't stop at the first); optionally save a gap-free photo series |
| `track_object(object_name, duration_s)` | Follow an object, staying above it |
| `record_video(seconds)` | Save an .mp4 from the camera |
| `fly_waypoints(waypoints, speed_m_s, smooth)` | Route through GPS points, offsets from home, saved places, or forward/right/up offsets from the start (for drawing letters and pictures, including vertically); per-point altitude and hover time |
| `measure_route(waypoints, return_home, max_total_m)` | Leg lengths, total, time, `within_limit` and a battery verdict, without flying |
| `plan_route(waypoints, return_home, start, max_total_m)` | Shortest visiting order (or farthest-first), without flying |
| `draw(points, plane, smooth)` | Draw a picture or letter from (x, y) canvas points, standing up (`vertical`) or flat |
| `fly_shape(shape, size_m, clockwise, width_m, pause_s)` | Triangle, square, rectangle, pentagon, hexagon, star, circle, figure eight; optional pause at corners |
| `face_location(latitude, longitude)` | Turn to face a point |
| `fly_home(altitude_m)` | Fly back over home and hover (no landing) |
| `land_at(latitude, longitude)` | Fly somewhere and land there |
| `save_location(name)`, `list_saved_locations`, `goto_saved_location(name, north_m, east_m)` | Remember places and go back ("10 m north of the person") |
| `release_payload`, `grab_payload` | Gripper (PX4 `DO_GRIPPER`) |

`move`, `move_relative` and `goto_location` also take an optional `speed_m_s`
for that one move.

Altitudes are metres above the launch point. Moves are limited to 1 km per
step, 2–120 m altitude, and a geofence of `drone.max_distance_from_home_m`
(500 m by default) so a misunderstood request can't send the drone away.

Objects the camera finds are saved as places automatically (`person`, or
`person_1`, `person_2`… from a survey), so "go back to the person" works
with `goto_saved_location`.

### Camera and object detection

The perception tools read the drone camera that Bevy streams on port 7002
(it points straight down; the top of the image is the drone's nose) and run a
COCO-trained YOLO11m on it. They need the optional extra, which installs
PyTorch and downloads the weights (~40 MB) to `models/` on first use:

```bash
uv sync --extra vision
uv run --extra vision scripts/tool.py takeoff altitude_m=15 + search_area object_name=person north_m=45 east_m=40
```

Detections come back with latitude/longitude and metres north/east of the
drone and of home, so they chain into `goto_location`, `orbit` or
`fly_to_object`. Each detection also has `color` (searches can filter on
it: "find the red car") and `size_m`, its length on the ground. The model was trained on ground-level photos, so from above it can
mislabel things (the forest mannequin often comes out as "kite"); asking for
a specific class (`object_name="person"`) avoids most of this, and
`survey_area` reports `also_seen_as` alternatives. Names are forgiving ("people", "a man" → `person`); for
something outside the 80 COCO classes the tools say so and suggest `look`,
which lets a vision LLM judge the image itself. Photos (annotated for
detections) are saved to `photos/`. In the `forest` world a person lies about
18 m north, 14 m east of the launch pad: a good first target.

### Try tools from the command line

`scripts/tool.py` calls tools exactly the way an LLM would, by name with
arguments. Chain calls with `+`:

```bash
uv run scripts/tool.py                       # list every tool
uv run scripts/tool.py takeoff altitude_m=10 + move north_m=15 + turn degrees=90 + land
```

## Build your agent

This branch is the starter. The drone toolkit, the model backends, the chat
CLI and the evals are done; two pieces of the agent are left for you, and
they are the parts that make it an agent (about an hour):

1. **The loop**: `Agent._loop` in
   [`src/bv_llm_sdk/agent/agent.py`](src/bv_llm_sdk/agent/agent.py). Ask the
   model, run the tools it asks for, give it the results, repeat until it
   answers in plain text. The docstring lists the helpers and steps. Done
   when the tests pass:
   ```bash
   uv run pytest tests/test_agent.py
   ```
2. **The system prompt**, in two halves the agent joins together:
   - `AGENT_RULES` in
     [`src/bv_llm_sdk/agent/prompt.py`](src/bv_llm_sdk/agent/prompt.py): how
     any tool-using agent should behave, whatever the robot.
   - `PROMPT` in
     [`src/bv_llm_sdk/toolkits/drone/toolkit.py`](src/bv_llm_sdk/toolkits/drone/toolkit.py):
     what the drone is and how its world works.

   The placeholders fly, badly. Run the quick evals against the simulator,
   read what failed, and add a rule to the right half for each mistake:
   ```bash
   uv run --extra agent --extra vision scripts/agent_eval.py --tags quick
   ```

You don't need to open anything else, but these are worth a look:
`models/ollama.py` (what one model call really sends), `messages.py` (the
conversation), `registry.py` (how a Python function becomes a tool
schema) and `toolkit.py` (everything the agent knows about a robot).

Finished early? Write your own tool (see [Adding a tool](#adding-a-tool)) and
ask the agent to use it, or try `--toolset core` with a small model and see
what changes. Or sketch a second robot (see [Other robots](#other-robots)):
your loop drives it unchanged. The finished agent is on the `ConstructI/O`
branch.

## The agent

`bv_llm_sdk.agent` turns plain-language requests into tool calls. It runs the
loop (model → tools → results → model) until the job is done, keeps the
conversation across requests, and stops a confused model from looping. It
flies the drone by default but can drive any robot (see
[Other robots](#other-robots)). It works with three backends:

| `--backend` | For | Default model |
| --- | --- | --- |
| `ollama` (default) | Ollama, e.g. served on OSC by [bv_llm_host](https://github.com/BuckeyeVertical/bv_llm_host) | `qwen3-coder:30b` |
| `openai` | Any OpenAI-compatible server (vLLM, OpenAI) | `gpt-oss:20b` |
| `anthropic` | Claude, a strong baseline (needs `ANTHROPIC_API_KEY`, `--extra claude`) | `claude-sonnet-5-5` |

### Run it against Ollama on OSC

```bash
# OSC login node, in bv_llm_host
scripts/submit.sh ollama
# laptop, in bv_llm_host (leave running)
scripts/tunnel.sh ollama
# laptop, in bv_llm_sdk, with the simulator running
uv sync --extra agent --extra vision
uv run --extra agent --extra vision bv-agent
```

```text
you> Take off to 6 meters, then draw a house in the sky in front of you: a square 10 m wide with a triangular roof.
  → takeoff(altitude_m=6)
    ✓ 11.0s  message: "hovering at 6 m"
  → draw(points=[{"x_m": 0, "y_m": 0}, {"x_m": 10, "y_m": 0}, {"x_m": 10, "y_m": 10}, {"x_m": 0, "y_m": 10}, {"x_m": 0, "y_m": 0}, {"x_m": 5, "y_m": 15}, {"x_m": 10, "y_m": 10}], plane="vertical")
    ✓ 44.5s  message: "drew 7 points in the vertical plane"
drone> I've taken off to 6 meters and drawn a house in the sky. The house is a square base that is 10 meters wide, with a triangular roof pointing upwards.
```

(Real output: `qwen3-coder:30b` on a Pitzer V100 through bv_llm_host, in the forest world.)

In the chat, `/tools`, `/status`, `/places`, `/reset` and `/quit` do what they
say. `--once "..."` runs a single request. Ctrl-C while it is working stops the
request and makes the drone hold position.

Options (also settable as `BV_AGENT_<NAME>` environment variables):

- `--toolset core|standard|full`: how many tools the model sees. Each schema
  costs prompt tokens (full is about 7k) and small models choose badly among
  many, so use `core` (12 tools) for models under about 10B parameters.
- `--num-ctx`: Ollama's context window (default 32768). Ollama silently drops
  whatever doesn't fit, which looks like a model ignoring its tools, so the
  agent uses Ollama's native API, which can set this.
- `--think true|low|medium|high`: let a reasoning model (qwen3, gpt-oss) think
  before answering. Slower, better at multi-step plans.

### From Python

```python
from bv_llm_sdk.agent import Agent, AgentConfig, make_model
from bv_llm_sdk.toolkits.drone import DroneToolkit

config = AgentConfig(backend="ollama", model="qwen3:32b", toolset="standard")
with DroneToolkit.connect() as drone:
    agent = Agent(drone, make_model(config), config, on_event=print)
    print(agent.run("Take off, search the field to the north for a person, then come back."))
```

`on_event` receives every step (`tool_call`, `tool_result`, `done`...), which
is the hook for a voice front end or a UI.

### Lessons from testing with a self-hosted model

Testing with `qwen3-coder:30b` shaped the toolkit as much as the agent did:

- **Put verdicts in tools, not in the model.** It read "234 m" and replied
  "well under 100 m", and called a survey needing 16 of 15 minutes "safe". So
  `measure_route`/`plan_route` return `within_limit`, and plans return
  `fits_in_battery` with the numbers and a sentence.
- **Name parameters so they can't be misread.** It took waypoint
  `forward_m` as steps from the previous point; renamed to
  `forward_of_start_m`. Asked for a drawing "in the vertical plane", it kept
  drawing flat until `draw` existed with an explicit `plane`.
- **Make results show what happened.** Routes return `ended_from_start_m`
  so the model can see a shape didn't close instead of assuming it did.
- **Expect tool calls as text.** Qwen3-Coder sometimes writes
  `<function=...>` XML in its reply instead of a real tool call; the agent
  parses that (and JSON) as a fallback.
- **Don't offer what the model can't use.** Text-only models don't get
  `look`, or they pretend to have seen the image.

### Evals: comparing models

`scripts/agent_eval.py` runs 16 scenarios in the forest world: basic flight,
speed, shapes and drawing, multi-turn conversations, finding and counting the
person, a distance-limited route, an impossible request and a battery
decision. Each scenario puts the drone in a known state first (on the pad or
hovering over it), sends the requests, and checks what the drone actually did.
Outcomes are PASS, FAIL (the model's fault) or ERROR (setup or server trouble,
not counted against the model). Every attempt is saved to
`evals/results/*.jsonl`.

```bash
uv run --extra agent --extra vision scripts/agent_eval.py --list           # the scenarios
uv run --extra agent --extra vision scripts/agent_eval.py --model qwen3.6:35b
uv run --extra agent --extra vision scripts/agent_eval.py --tags quick --repeat 3
# vLLM or any OpenAI-compatible server, same scenarios:
uv run --extra agent --extra vision scripts/agent_eval.py --backend openai \
    --base-url http://localhost:8000/v1 --model Qwen/Qwen3-32B
uv run scripts/eval_report.py --failures                                   # compare all runs
```

Results so far (Ollama on Pitzer V100s; see [evals/RESULTS.md](evals/RESULTS.md)):
`qwen3.6:35b`, `qwen3.8:27b` and `gpt-oss:20b` passed every scenario; `qwen3.5:122b`,
`qwen3:32b`, `gpt-oss:120b` and `qwen3-coder:30b` did well on the quick set;
`llama3.3:70b` was weakest. `qwen3.8:27b` needs an A100/H100 and Ollama
newer than OSC's module (see evals/RESULTS.md).

To add a scenario, append to `SCENARIOS` in
`src/bv_llm_sdk/toolkits/drone/evals/scenarios.py`: the request in a person's words, a
starting state, and a check on the drone's actual state.

On Pitzer, which has no Ollama module, bv_llm_host runs OSC's Ollama
Apptainer image instead (`OLLAMA_SIF`); V100s usually start much sooner than
Cardinal's GPUs.

### Without the agent

To build your own loop, `tool_schemas("anthropic" | "openai")` gives the tool
definitions and `run_tool(drone, name, args)` runs one and returns JSON
(`run_tool_blocks` returns Anthropic content blocks, including images from
`look`).

### Other robots

The agent loop knows nothing about drones. It drives a `Toolkit`
([`src/bv_llm_sdk/toolkit.py`](src/bv_llm_sdk/toolkit.py)): the robot's tools,
its half of the system prompt (what it is, how its world works, rules for its
tools), a state line sent with each request, and how to stop it safely. The
agent adds the half of the prompt every robot shares
([`agent/prompt.py`](src/bv_llm_sdk/agent/prompt.py)). The drone is
[`toolkits/drone/`](src/bv_llm_sdk/toolkits/drone/).

A robot arm, say, needs its own registry, tools and toolkit:

```python
from bv_llm_sdk.registry import ToolError, ToolRegistry
from bv_llm_sdk.toolkit import Toolkit

arm_tools = ToolRegistry()

@arm_tools.tool
def move_to(arm: Arm, x_m: float, y_m: float, z_m: float) -> dict:
    """Move the gripper to a point, in metres from the arm's base.

    Args:
        x_m: Forward from the base.
        y_m: Left of the base.
        z_m: Up from the table.
    """
    if not arm.reachable(x_m, y_m, z_m):
        raise ToolError(f"({x_m}, {y_m}, {z_m}) is out of reach")
    arm.move_to(x_m, y_m, z_m)
    return {"position_m": arm.position()}

class ArmToolkit(Toolkit):
    name = "arm"
    registry = arm_tools
    prompt = "You control a six-axis robot arm on a table. ..."

    def state(self):
        return f"Arm state: gripper at {self.robot.position()}"

    def stop(self):
        self.robot.halt()
```

Then add it to `TOOLKITS` in
[`toolkits/__init__.py`](src/bv_llm_sdk/toolkits/__init__.py) for
`bv-agent --toolkit arm`, or pass `ArmToolkit(arm)` to `Agent` directly. The
lessons above carry over: put verdicts like `reachable` in the tools, and
make results show what actually happened (did the grasp hold?).

## Developing

### Layout

```text
src/bv_llm_sdk/
├── registry.py        ToolRegistry: Python functions -> LLM tool schemas
├── toolkit.py         Toolkit: everything the agent knows about one robot
├── agent/             the agent loop, prompt rules, model backends, bv-agent CLI
└── toolkits/
    └── drone/         one robot, self-contained
        ├── drone.py       the PX4 link (Drone, connect)
        ├── tools.py       the drone's ToolRegistry (@tool, call_tool...)
        ├── actions/       flight, navigation, routes, perception, payload tools
        ├── telemetry.py   status and position tools
        ├── camera.py, vision.py, geo.py
        ├── toolkit.py     DroneToolkit: prompt, toolsets, state line, stop
        └── evals/         scenarios in the forest world
```

Nothing under `agent/` imports a robot. A new robot goes in its own
`toolkits/<name>/`.

### Adding a tool

Write a function with type hints and a Google-style docstring and decorate it
with `@tool`. The LLM schema is generated from the function name, the
docstring, and the `Args:` section, so the docstring *is* the prompt. Then
import it in `src/bv_llm_sdk/toolkits/drone/actions/__init__.py`.

```python
from bv_llm_sdk.toolkits.drone import Drone, move, tool

@tool
def fly_square(drone: Drone, side_m: float = 10.0) -> dict:
    """Fly a square, ending where it started.

    Args:
        side_m: Length of each side in metres.
    """
    for north, east in ((side_m, 0), (0, side_m), (-side_m, 0), (0, -side_m)):
        move(drone, north_m=north, east_m=east)
    return {"message": "square done"}
```

Low-level building blocks for new tools live on `Drone`:
`drone.command_long(...)` / `drone.command_int(...)` send a command and wait for
PX4 to accept it, `drone.latest("GLOBAL_POSITION_INT")` / `drone.wait_for(...)`
read telemetry, and `drone.armed`, `drone.flight_mode`, `drone.landed_state`
give the vehicle state. The comments at the top of `toolkits/drone/drone.py` explain the
non-obvious parts of the PX4 link.

### Tests

```bash
uv run pytest        # offline checks, no simulator needed
```
